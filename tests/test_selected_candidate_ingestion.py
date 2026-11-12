from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedAuthor, PubMedRecord
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    PaperTask,
    PipelineRun,
    ResearchDomain,
    SearchQuery,
    SourceRecord,
)
from literature_intelligence.pipeline.selected_candidate_ingestion import (
    SelectedCandidateIngestion,
)
from literature_intelligence.services.verification import VerificationResult


def _record(pmid: str, doi: str) -> PubMedRecord:
    return PubMedRecord(
        pmid=pmid,
        title=f"Test paper {pmid}",
        abstract=f"Abstract evidence for {pmid}.",
        journal="Test Journal",
        publication_year=2026,
        publication_month="Nov",
        publication_day=1,
        medline_date=None,
        doi=doi,
        pmcid=None,
        language="eng",
        publication_types=("Journal Article",),
        authors=(PubMedAuthor(display_name="Test Author"),),
    )


class FakePubMed:
    def __init__(self) -> None:
        self.records = {
            "10000001": _record("10000001", "10.1000/one"),
            "10000002": _record("10000002", "10.1000/two"),
        }

    def fetch(self, pmids):
        return [self.records[pmid] for pmid in pmids if pmid in self.records]


class FakeVerifier:
    def verify_pmid(self, value: str) -> VerificationResult:
        return VerificationResult("pmid", value, "valid")

    def verify_doi(self, value: str) -> VerificationResult:
        return VerificationResult("doi", value, "valid", resolved_url=f"https://doi.org/{value}")

    def verify_url(self, value: str) -> VerificationResult:
        return VerificationResult("url", value, "valid", resolved_url=value)


class SelectedCandidateIngestionTests(unittest.TestCase):
    def test_ingests_multiple_domains_into_one_pipeline_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_database(f"sqlite:///{(root / 'test.db').as_posix()}")
            try:
                with Session(engine) as session, session.begin():
                    session.add_all(
                        [
                            ResearchDomain(code="gut_health", name_zh="肠道", is_active=True),
                            ResearchDomain(code="immune_health", name_zh="免疫", is_active=True),
                        ]
                    )
                selection = root / "selection.json"
                selection.write_text(
                    json.dumps(
                        {
                            "schema_version": "0.1",
                            "batch_id": "batch-test",
                            "edition_date": "2026-11-12",
                            "selected": [
                                {
                                    "pmid": "10000001",
                                    "primary_domain": "gut_health",
                                    "selection_reason": "gut test",
                                },
                                {
                                    "pmid": "10000002",
                                    "primary_domain": "immune_health",
                                    "selection_reason": "immune test",
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                summary = SelectedCandidateIngestion(
                    engine=engine,
                    pubmed=FakePubMed(),
                    verifier=FakeVerifier(),
                ).run(selection)
                self.assertEqual(summary.requested_count, 2)
                self.assertEqual(summary.fetched_count, 2)
                self.assertEqual(summary.task_counts, {"VERIFIED": 2})
                with Session(engine) as session:
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(PipelineRun)), 1
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(SearchQuery)), 2
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(SourceRecord)), 2
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(PaperTask)), 2
                    )
                    run_ids = set(session.scalars(select(PaperTask.pipeline_run_id)))
                    self.assertEqual(run_ids, {summary.pipeline_run_id})
                    query_domains = set(
                        session.scalars(
                            select(ResearchDomain.code)
                            .join(SearchQuery, SearchQuery.domain_id == ResearchDomain.id)
                            .join(SourceRecord, SourceRecord.search_query_id == SearchQuery.id)
                        )
                    )
                    self.assertEqual(query_domains, {"gut_health", "immune_health"})
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
