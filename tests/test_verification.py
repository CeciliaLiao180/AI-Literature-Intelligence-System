from __future__ import annotations

import json
import unittest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedRecord
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import Paper, PaperTask, PipelineRun, VerificationRecord
from literature_intelligence.services.verification import (
    HTTPResult,
    UnsafeURLError,
    VerificationResult,
    VerificationService,
    persist_verification_results,
    validate_public_http_url,
)


def record(pmid: str = "12345678") -> PubMedRecord:
    return PubMedRecord(
        pmid=pmid,
        title="Verified title",
        abstract=None,
        journal=None,
        publication_year=None,
        publication_month=None,
        publication_day=None,
        medline_date=None,
        doi=None,
        pmcid=None,
        language=None,
        publication_types=(),
        authors=(),
    )


class FakePubMedClient:
    def __init__(self, records: list[PubMedRecord]) -> None:
        self.records = records

    def fetch(self, pmids):  # type: ignore[no-untyped-def]
        return [item for item in self.records if item.pmid in set(pmids)]


class VerificationTests(unittest.TestCase):
    def test_validates_pmid_by_exact_efetch_record(self) -> None:
        service = VerificationService(
            pubmed_client=FakePubMedClient([record()]),  # type: ignore[arg-type]
            http_transport=lambda *_: HTTPResult(200, "https://example.org"),
        )
        self.assertEqual(service.verify_pmid("12345678").status, "valid")
        self.assertEqual(service.verify_pmid("99999999").status, "invalid")
        self.assertEqual(service.verify_pmid("bad").status, "invalid")

    def test_validates_crossref_doi_and_normalizes_case(self) -> None:
        payload = {"message": {"DOI": "10.1000/EXAMPLE", "title": ["Example"]}}

        def transport(url: str, method: str, _: float) -> HTTPResult:
            self.assertIn("api.crossref.org/works/", url)
            self.assertEqual(method, "GET")
            return HTTPResult(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

        service = VerificationService(
            pubmed_client=FakePubMedClient([]),  # type: ignore[arg-type]
            http_transport=transport,
        )
        result = service.verify_doi("https://doi.org/10.1000/example")
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.target, "10.1000/example")

    def test_doi_mismatch_is_conflict(self) -> None:
        def transport(url: str, _method: str, _timeout: float) -> HTTPResult:
            payload = {"message": {"DOI": "10.1000/other"}}
            return HTTPResult(200, url, body=json.dumps(payload).encode())

        service = VerificationService(
            pubmed_client=FakePubMedClient([]),  # type: ignore[arg-type]
            http_transport=transport,
        )
        self.assertEqual(service.verify_doi("10.1000/example").status, "conflict")

    def test_url_status_mapping(self) -> None:
        statuses = iter([200, 404, 503, 403])

        def transport(url: str, _method: str, _timeout: float) -> HTTPResult:
            return HTTPResult(next(statuses), url)

        service = VerificationService(
            pubmed_client=FakePubMedClient([]),  # type: ignore[arg-type]
            http_transport=transport,
        )
        self.assertEqual(service.verify_url("https://example.org/a").status, "valid")
        self.assertEqual(service.verify_url("https://example.org/b").status, "invalid")
        self.assertEqual(service.verify_url("https://example.org/c").status, "temporary_error")
        self.assertEqual(service.verify_url("https://example.org/d").status, "conflict")

    def test_rejects_private_and_non_http_urls(self) -> None:
        for url in ("http://127.0.0.1/a", "http://[::1]/", "file:///etc/passwd"):
            with self.assertRaises(UnsafeURLError):
                validate_public_http_url(url)

    def test_persists_append_only_verification_records(self) -> None:
        engine = create_database("sqlite:///:memory:")
        try:
            with Session(engine) as session:
                run = PipelineRun(trigger_type="manual", status="PROCESSING", config_snapshot={})
                paper = Paper(canonical_title="Title", normalized_title="title")
                session.add_all([run, paper])
                session.flush()
                task = PaperTask(pipeline_run_id=run.id, paper_id=paper.id, state="VERIFYING")
                session.add(task)
                session.flush()
                persist_verification_results(
                    session,
                    task.id,
                    [VerificationResult(check_type="pmid", target="123", status="valid")],
                )
                session.commit()
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(VerificationRecord)), 1
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
