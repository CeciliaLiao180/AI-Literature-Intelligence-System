from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import date

from literature_intelligence.collectors.pubmed import PubMedAuthor, PubMedRecord


DOI_URL_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
DOI_LABEL_PREFIX = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
TITLE_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)
YEAR_PATTERN = re.compile(r"\b(18|19|20|21)\d{2}\b")

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class NormalizedAuthor:
    display_name: str
    normalized_name: str
    affiliation: str | None
    orcid: str | None


@dataclass(frozen=True)
class NormalizedPaper:
    canonical_title: str
    normalized_title: str
    abstract: str | None
    journal_name: str | None
    publication_date: date | None
    publication_date_precision: str
    language: str | None
    article_type: str | None
    pubmed_url: str
    publisher_url: str | None
    identifiers: dict[str, str]
    authors: tuple[NormalizedAuthor, ...]


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(unicodedata.normalize("NFKC", html.unescape(value)).split())
    return cleaned or None


def normalize_title(value: str) -> str:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        raise ValueError("paper title must not be blank")
    folded = unicodedata.normalize("NFKC", cleaned).casefold()
    return " ".join(TITLE_SEPARATORS.sub(" ", folded).split())


def normalize_doi(value: str | None) -> str | None:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        return None
    decoded = urllib.parse.unquote(cleaned)
    decoded = DOI_LABEL_PREFIX.sub("", decoded)
    decoded = DOI_URL_PREFIX.sub("", decoded)
    normalized = decoded.strip().rstrip(".,;").casefold()
    return normalized or None


def normalize_pmid(value: str | None) -> str | None:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        return None
    return cleaned if cleaned.isdigit() else None


def normalize_pmcid(value: str | None) -> str | None:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        return None
    candidate = cleaned.upper()
    if not candidate.startswith("PMC"):
        candidate = "PMC" + candidate
    return candidate if candidate[3:].isdigit() else None


def normalize_orcid(value: str | None) -> str | None:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        return None
    for prefix in ("https://orcid.org/", "http://orcid.org/"):
        if cleaned.casefold().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.upper()


def parse_publication_date(record: PubMedRecord) -> tuple[date | None, str]:
    year = record.publication_year
    month = _parse_month(record.publication_month)
    day = record.publication_day

    if year is None and record.medline_date:
        year_match = YEAR_PATTERN.search(record.medline_date)
        year = int(year_match.group(0)) if year_match else None
        month_tokens = re.findall(r"[A-Za-z]+", record.medline_date)
        month = next((_parse_month(token) for token in month_tokens if _parse_month(token)), None)

    if year is None:
        return None, "unknown"
    if month is None:
        return date(year, 1, 1), "year"
    if day is None:
        return date(year, month, 1), "month"
    try:
        return date(year, month, day), "day"
    except ValueError:
        return date(year, month, 1), "month"


def _parse_month(value: str | None) -> int | None:
    cleaned = normalize_whitespace(value)
    if not cleaned:
        return None
    if cleaned.isdigit():
        numeric = int(cleaned)
        return numeric if 1 <= numeric <= 12 else None
    return MONTHS.get(cleaned.casefold())


def map_article_type(publication_types: tuple[str, ...]) -> str | None:
    folded = {value.casefold() for value in publication_types}
    priorities = (
        ("randomized controlled trial", "randomized_controlled_trial"),
        ("clinical trial", "clinical_trial"),
        ("meta-analysis", "meta_analysis"),
        ("systematic review", "systematic_review"),
        ("observational study", "observational"),
        ("review", "review"),
        ("journal article", "journal_article"),
    )
    for source_value, target_value in priorities:
        if source_value in folded:
            return target_value
    return "other" if folded else None


def normalize_author(author: PubMedAuthor) -> NormalizedAuthor:
    display_name = normalize_whitespace(author.display_name)
    if not display_name:
        raise ValueError("author display name must not be blank")
    return NormalizedAuthor(
        display_name=display_name,
        normalized_name=normalize_title(display_name),
        affiliation=normalize_whitespace(author.affiliation),
        orcid=normalize_orcid(author.orcid),
    )


def normalize_pubmed_record(record: PubMedRecord) -> NormalizedPaper:
    pmid = normalize_pmid(record.pmid)
    if not pmid:
        raise ValueError("PubMed record has an invalid PMID")
    canonical_title = normalize_whitespace(record.title)
    if not canonical_title:
        raise ValueError("PubMed record has no title")
    doi = normalize_doi(record.doi)
    pmcid = normalize_pmcid(record.pmcid)
    publication_date, precision = parse_publication_date(record)
    identifiers = {"pmid": pmid}
    if doi:
        identifiers["doi"] = doi
    if pmcid:
        identifiers["pmcid"] = pmcid
    return NormalizedPaper(
        canonical_title=canonical_title,
        normalized_title=normalize_title(canonical_title),
        abstract=normalize_whitespace(record.abstract),
        journal_name=normalize_whitespace(record.journal),
        publication_date=publication_date,
        publication_date_precision=precision,
        language=normalize_whitespace(record.language),
        article_type=map_article_type(record.publication_types),
        pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        publisher_url=f"https://doi.org/{doi}" if doi else None,
        identifiers=identifiers,
        authors=tuple(normalize_author(author) for author in record.authors),
    )


def build_abstract_snapshot(normalized: NormalizedPaper) -> tuple[list[dict[str, str]], str] | None:
    if not normalized.abstract:
        return None
    sections = [{"section_id": "abstract", "name": "Abstract", "text": normalized.abstract}]
    return sections, compute_sections_sha256(sections)


def compute_sections_sha256(sections: list[dict[str, str]]) -> str:
    canonical_json = json.dumps(sections, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
