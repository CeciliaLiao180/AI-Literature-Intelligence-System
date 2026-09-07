from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable


EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}


class PubMedError(RuntimeError):
    """Base error for PubMed client failures."""


class PubMedResponseError(PubMedError):
    """Raised for invalid or unexpected PubMed responses."""


@dataclass(frozen=True)
class PubMedSearchResult:
    query: str
    count: int
    pmids: tuple[str, ...]


@dataclass(frozen=True)
class PubMedAuthor:
    display_name: str
    last_name: str | None = None
    fore_name: str | None = None
    initials: str | None = None
    affiliation: str | None = None
    orcid: str | None = None


@dataclass(frozen=True)
class PubMedRecord:
    pmid: str
    title: str
    abstract: str | None
    journal: str | None
    publication_year: int | None
    publication_month: str | None
    publication_day: int | None
    medline_date: str | None
    doi: str | None
    pmcid: str | None
    language: str | None
    publication_types: tuple[str, ...]
    authors: tuple[PubMedAuthor, ...]
    publication_date_source: str = "Article/Journal/JournalIssue/PubDate"
    publication_model: str | None = None
    citation_status: str | None = None
    article_dates: tuple[dict[str, str | None], ...] = ()
    history_dates: tuple[dict[str, str | None], ...] = ()

    def to_source_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["publication_types"] = list(self.publication_types)
        payload["authors"] = [asdict(author) for author in self.authors]
        payload["article_dates"] = list(self.article_dates)
        payload["history_dates"] = list(self.history_dates)
        return payload


Transport = Callable[[str, dict[str, str | int], float], bytes]


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext())
    cleaned = " ".join(value.split())
    return cleaned or None


def _first_text(parent: ET.Element, path: str) -> str | None:
    return _text(parent.find(path))


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class PubMedClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "ai_literature_intelligence",
        timeout_seconds: float = 20.0,
        max_attempts: int = 4,
        transport: Transport | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.api_key = api_key
        self.email = email
        self.tool = tool
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._transport = transport or self._default_transport
        self._min_interval = 0.11 if api_key else 0.34
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0

    def search(
        self,
        term: str,
        *,
        retmax: int = 20,
        mindate: str | None = None,
        maxdate: str | None = None,
        datetype: str = "pdat",
        sort: str = "pub date",
    ) -> PubMedSearchResult:
        if not term.strip():
            raise ValueError("term must not be blank")
        if not 1 <= retmax <= 10_000:
            raise ValueError("retmax must be between 1 and 10000")

        params: dict[str, str | int] = {
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": retmax,
            "sort": sort,
        }
        if mindate:
            params["mindate"] = mindate
            params["datetype"] = datetype
        if maxdate:
            params["maxdate"] = maxdate
            params["datetype"] = datetype

        raw = self._request("esearch.fcgi", params)
        try:
            payload = json.loads(raw.decode("utf-8"))
            result = payload["esearchresult"]
            pmids = tuple(str(value) for value in result.get("idlist", []))
            count = int(result["count"])
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise PubMedResponseError("Invalid ESearch JSON response") from exc
        return PubMedSearchResult(query=term, count=count, pmids=pmids)

    def fetch(self, pmids: Iterable[str]) -> list[PubMedRecord]:
        normalized_ids = tuple(dict.fromkeys(str(pmid).strip() for pmid in pmids if str(pmid).strip()))
        if not normalized_ids:
            return []
        if len(normalized_ids) > 200:
            raise ValueError("fetch accepts at most 200 PMIDs per request")
        if any(not pmid.isdigit() for pmid in normalized_ids):
            raise ValueError("all PMIDs must contain digits only")

        raw = self._request(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(normalized_ids),
                "retmode": "xml",
            },
        )
        return parse_pubmed_xml(raw)

    def search_and_fetch(self, term: str, *, retmax: int = 20) -> list[PubMedRecord]:
        result = self.search(term, retmax=retmax)
        return self.fetch(result.pmids)

    def _request(self, endpoint: str, params: dict[str, str | int]) -> bytes:
        request_params = dict(params)
        request_params["tool"] = self.tool
        if self.email:
            request_params["email"] = self.email
        if self.api_key:
            request_params["api_key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_rate_limit()
            try:
                return self._transport(endpoint, request_params, self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in TRANSIENT_HTTP_STATUSES or attempt == self.max_attempts:
                    raise PubMedError(f"PubMed HTTP error {exc.code}") from exc
                delay = self._retry_delay(attempt, exc.headers.get("Retry-After"))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    raise PubMedError("PubMed request failed after retries") from exc
                delay = self._retry_delay(attempt, None)
            time.sleep(delay)
        raise PubMedError("PubMed request failed") from last_error

    def _wait_for_rate_limit(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                    return max(0.0, retry_time.timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(30.0, float(2 ** (attempt - 1)))

    @staticmethod
    def _default_transport(
        endpoint: str, params: dict[str, str | int], timeout_seconds: float
    ) -> bytes:
        url = EUTILS_BASE_URL + endpoint + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/xml;q=0.9",
                "User-Agent": "AI-Literature-Intelligence-System/0.1",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read()


def parse_pubmed_xml(raw_xml: bytes | str) -> list[PubMedRecord]:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise PubMedResponseError("Invalid EFetch XML response") from exc

    records: list[PubMedRecord] = []
    for item in root.findall("./PubmedArticle"):
        citation = item.find("./MedlineCitation")
        article = item.find("./MedlineCitation/Article")
        if citation is None or article is None:
            continue

        pmid = _first_text(citation, "./PMID")
        title = _first_text(article, "./ArticleTitle")
        if not pmid or not title:
            continue

        abstract_parts: list[str] = []
        for abstract_element in article.findall("./Abstract/AbstractText"):
            text = _text(abstract_element)
            if not text:
                continue
            label = abstract_element.attrib.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(abstract_parts) or None

        journal = _first_text(article, "./Journal/Title")
        pub_date = article.find("./Journal/JournalIssue/PubDate")
        publication_year = _parse_int(_first_text(pub_date, "./Year")) if pub_date is not None else None
        publication_month = _first_text(pub_date, "./Month") if pub_date is not None else None
        publication_day = _parse_int(_first_text(pub_date, "./Day")) if pub_date is not None else None
        medline_date = _first_text(pub_date, "./MedlineDate") if pub_date is not None else None

        identifiers: dict[str, str] = {}
        for identifier in item.findall("./PubmedData/ArticleIdList/ArticleId"):
            value = _text(identifier)
            id_type = identifier.attrib.get("IdType", "").lower()
            if value and id_type:
                identifiers[id_type] = value
        if "doi" not in identifiers:
            for location_id in article.findall("./ELocationID"):
                if location_id.attrib.get("EIdType", "").lower() == "doi":
                    value = _text(location_id)
                    if value:
                        identifiers["doi"] = value
                        break

        authors: list[PubMedAuthor] = []
        for author in article.findall("./AuthorList/Author"):
            collective = _first_text(author, "./CollectiveName")
            last_name = _first_text(author, "./LastName")
            fore_name = _first_text(author, "./ForeName")
            initials = _first_text(author, "./Initials")
            display_name = collective or " ".join(part for part in (fore_name, last_name) if part)
            if not display_name:
                continue
            affiliation = _first_text(author, "./AffiliationInfo/Affiliation")
            orcid = None
            for identifier in author.findall("./Identifier"):
                if identifier.attrib.get("Source", "").lower() == "orcid":
                    orcid = _text(identifier)
                    break
            authors.append(
                PubMedAuthor(
                    display_name=display_name,
                    last_name=last_name,
                    fore_name=fore_name,
                    initials=initials,
                    affiliation=affiliation,
                    orcid=orcid,
                )
            )

        publication_types = tuple(
            text
            for element in article.findall("./PublicationTypeList/PublicationType")
            if (text := _text(element))
        )
        language = _first_text(article, "./Language")

        article_dates = tuple(
            {
                "date_type": element.attrib.get("DateType"),
                "year": _first_text(element, "./Year"),
                "month": _first_text(element, "./Month"),
                "day": _first_text(element, "./Day"),
            }
            for element in article.findall("./ArticleDate")
        )
        history_dates = tuple(
            {
                "pub_status": element.attrib.get("PubStatus"),
                "year": _first_text(element, "./Year"),
                "month": _first_text(element, "./Month"),
                "day": _first_text(element, "./Day"),
                "hour": _first_text(element, "./Hour"),
                "minute": _first_text(element, "./Minute"),
            }
            for element in item.findall("./PubmedData/History/PubMedPubDate")
        )

        records.append(
            PubMedRecord(
                pmid=pmid,
                title=title,
                abstract=abstract,
                journal=journal,
                publication_year=publication_year,
                publication_month=publication_month,
                publication_day=publication_day,
                medline_date=medline_date,
                doi=identifiers.get("doi"),
                pmcid=identifiers.get("pmc"),
                language=language,
                publication_types=publication_types,
                authors=tuple(authors),
                publication_model=article.attrib.get("PubModel"),
                citation_status=citation.attrib.get("Status"),
                article_dates=article_dates,
                history_dates=history_dates,
            )
        )
    return records
