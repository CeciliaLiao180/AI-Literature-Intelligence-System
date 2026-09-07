from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedClient, PubMedError
from literature_intelligence.db.models import VerificationRecord
from literature_intelligence.services.normalization import normalize_doi, normalize_pmid


DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class UnsafeURLError(ValueError):
    pass


@dataclass(frozen=True)
class HTTPResult:
    status_code: int
    final_url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class VerificationResult:
    check_type: str
    target: str
    status: str
    http_status: int | None = None
    resolved_url: str | None = None
    response_fingerprint: str | None = None
    details: dict[str, object] = field(default_factory=dict)


HTTPTransport = Callable[[str, str, float], HTTPResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class SafeHTTPClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_redirects: int = 5,
        user_agent: str = "AI-Literature-Intelligence-System/0.1",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, url: str, method: str = "HEAD", timeout: float | None = None) -> HTTPResult:
        total_timeout = timeout or self.timeout_seconds
        deadline = time.monotonic() + total_timeout
        current_url = url
        request_method = method.upper()
        redirect_count = 0
        used_get_fallback = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("URL verification exceeded its total timeout")
            validate_public_http_url(current_url)
            per_io_timeout = max(0.5, min(remaining, total_timeout / 3))
            try:
                result = self._single_request(
                    current_url, request_method, per_io_timeout
                )
            except urllib.error.HTTPError as exc:
                result = HTTPResult(
                    status_code=exc.code,
                    final_url=current_url,
                    headers={key.lower(): value for key, value in exc.headers.items()},
                    body=exc.read(65_536),
                )

            if result.status_code in REDIRECT_STATUSES:
                redirect_count += 1
                if redirect_count > self.max_redirects:
                    raise urllib.error.HTTPError(
                        current_url, 310, "too many redirects", hdrs=None, fp=None
                    )
                location = result.headers.get("location")
                if not location:
                    return result
                current_url = urllib.parse.urljoin(current_url, location)
                request_method = "GET" if result.status_code == 303 else request_method
                continue
            if (
                method.upper() == "HEAD"
                and request_method == "HEAD"
                and result.status_code in {403, 405}
                and not used_get_fallback
            ):
                request_method = "GET"
                used_get_fallback = True
                continue
            return result

    def _single_request(self, url: str, method: str, timeout: float) -> HTTPResult:
        headers = {
            "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "User-Agent": self.user_agent,
        }
        if method == "GET":
            headers["Range"] = "bytes=0-65535"
        request = urllib.request.Request(url, headers=headers, method=method)
        with self._opener.open(request, timeout=timeout) as response:
            body = response.read(65_536) if method == "GET" else b""
            return HTTPResult(
                status_code=response.status,
                final_url=response.geturl(),
                headers={key.lower(): value for key, value in response.headers.items()},
                body=body,
            )


def validate_public_http_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("URL scheme must be http or https")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL credentials are not allowed")
    if not parsed.hostname:
        raise UnsafeURLError("URL hostname is required")
    host = parsed.hostname.rstrip(".")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise UnsafeURLError("localhost URLs are not allowed")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as exc:
            raise UnsafeURLError("URL hostname could not be resolved") from exc
    if not addresses:
        raise UnsafeURLError("URL hostname resolved to no addresses")
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise UnsafeURLError("URL resolves to a non-public address")


class VerificationService:
    def __init__(
        self,
        *,
        pubmed_client: PubMedClient,
        http_transport: HTTPTransport | None = None,
        crossref_email: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.pubmed_client = pubmed_client
        self.timeout_seconds = timeout_seconds
        self.crossref_email = crossref_email
        self._http = http_transport or SafeHTTPClient(
            timeout_seconds=timeout_seconds,
            user_agent=self._user_agent(crossref_email),
        ).request

    @staticmethod
    def _user_agent(email: str | None) -> str:
        base = "AI-Literature-Intelligence-System/0.1"
        return f"{base} (mailto:{email})" if email else base

    def verify_pmid(self, value: str) -> VerificationResult:
        pmid = normalize_pmid(value)
        if pmid is None:
            return VerificationResult(
                check_type="pmid",
                target=value,
                status="invalid",
                details={"reason": "invalid_format"},
            )
        try:
            records = self.pubmed_client.fetch([pmid])
        except PubMedError as exc:
            return VerificationResult(
                check_type="pmid",
                target=pmid,
                status="temporary_error",
                details={"reason": "pubmed_unavailable", "error_type": type(exc).__name__},
            )
        if len(records) == 1 and records[0].pmid == pmid:
            return VerificationResult(
                check_type="pmid",
                target=pmid,
                status="valid",
                resolved_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                details={"title": records[0].title},
            )
        return VerificationResult(
            check_type="pmid",
            target=pmid,
            status="invalid",
            details={"reason": "not_found"},
        )

    def verify_doi(self, value: str) -> VerificationResult:
        doi = normalize_doi(value)
        if doi is None or not DOI_PATTERN.fullmatch(doi):
            return VerificationResult(
                check_type="doi",
                target=value,
                status="invalid",
                details={"reason": "invalid_format"},
            )

        encoded_doi = urllib.parse.quote(doi, safe="")
        query = {}
        if self.crossref_email:
            query["mailto"] = self.crossref_email
        crossref_url = f"https://api.crossref.org/works/{encoded_doi}"
        if query:
            crossref_url += "?" + urllib.parse.urlencode(query)
        try:
            response = self._http(crossref_url, "GET", self.timeout_seconds)
        except (OSError, UnsafeURLError, urllib.error.URLError):
            return VerificationResult(
                check_type="doi",
                target=doi,
                status="temporary_error",
                details={"reason": "crossref_unavailable"},
            )

        fingerprint = _fingerprint(response.body)
        if 200 <= response.status_code < 300:
            try:
                payload = json.loads(response.body.decode("utf-8"))
                returned_doi = normalize_doi(payload["message"]["DOI"])
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                return VerificationResult(
                    check_type="doi",
                    target=doi,
                    status="conflict",
                    http_status=response.status_code,
                    resolved_url=response.final_url,
                    response_fingerprint=fingerprint,
                    details={"reason": "invalid_crossref_payload"},
                )
            status = "valid" if returned_doi == doi else "conflict"
            return VerificationResult(
                check_type="doi",
                target=doi,
                status=status,
                http_status=response.status_code,
                resolved_url=f"https://doi.org/{doi}",
                response_fingerprint=fingerprint,
                details={
                    "registry": "crossref",
                    "returned_doi": returned_doi,
                    "title": _crossref_title(payload),
                },
            )
        if response.status_code in TRANSIENT_HTTP_STATUSES:
            status = "temporary_error"
        elif response.status_code in {404, 410}:
            status = "invalid"
        else:
            status = "conflict"
        return VerificationResult(
            check_type="doi",
            target=doi,
            status=status,
            http_status=response.status_code,
            resolved_url=response.final_url,
            response_fingerprint=fingerprint,
            details={"reason": "crossref_status"},
        )

    def verify_url(self, value: str) -> VerificationResult:
        try:
            response = self._http(value, "HEAD", self.timeout_seconds)
        except UnsafeURLError as exc:
            return VerificationResult(
                check_type="url",
                target=value,
                status="invalid",
                details={"reason": "unsafe_url", "message": str(exc)},
            )
        except (OSError, urllib.error.URLError):
            return VerificationResult(
                check_type="url",
                target=value,
                status="temporary_error",
                details={"reason": "network_error"},
            )

        if 200 <= response.status_code < 400:
            status = "valid"
        elif response.status_code in TRANSIENT_HTTP_STATUSES:
            status = "temporary_error"
        elif response.status_code in {401, 403}:
            status = "conflict"
        else:
            status = "invalid"
        return VerificationResult(
            check_type="url",
            target=value,
            status=status,
            http_status=response.status_code,
            resolved_url=response.final_url,
            response_fingerprint=_fingerprint(response.body),
            details={"content_type": response.headers.get("content-type")},
        )


def persist_verification_results(
    session: Session, paper_task_id: str, results: list[VerificationResult]
) -> list[VerificationRecord]:
    rows = [
        VerificationRecord(
            paper_task_id=paper_task_id,
            check_type=result.check_type,
            target=result.target,
            status=result.status,
            http_status=result.http_status,
            resolved_url=result.resolved_url,
            response_fingerprint=result.response_fingerprint,
            details=result.details,
        )
        for result in results
    ]
    session.add_all(rows)
    session.flush()
    return rows


def _fingerprint(body: bytes) -> str | None:
    return hashlib.sha256(body).hexdigest() if body else None


def _crossref_title(payload: dict[str, object]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    title = message.get("title")
    if isinstance(title, list) and title and isinstance(title[0], str):
        return title[0]
    return None
