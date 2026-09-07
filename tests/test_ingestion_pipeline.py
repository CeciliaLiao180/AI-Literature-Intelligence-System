from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import (
    PubMedAuthor,
    PubMedRecord,
    PubMedSearchResult,
)
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    ContentSnapshot,
    Paper,
    PaperTask,
    PipelineRun,
    ResearchDomain,
    SourceRecord,
    TaskStateEvent,
    VerificationRecord,
)
from literature_intelligence.pipeline.ingestion import (
    IngestionConfig,
    IngestionPipeline,
)
from literature_intelligence.services.verification import VerificationResult


def sample_record() -> PubMedRecord:
    return PubMedRecord(
        pmid="42520140",
        title="Effect of probiotics on humoral responses.",
        abstract="A randomized placebo-controlled trial.",
        journal="Gut Microbes",
        publication_year=2026,
        publication_month="Jan",
        publication_day=1,
        medline_date=None,
        doi="10.1080/19490976.2026.2699456",
        pmcid=None,
        language="eng",
        publication_types=("Randomized Controlled Trial",),
        authors=(PubMedAuthor(display_name="Example Author"),),
    )


class FakePubMed:
    def search(self, term: str, *, retmax: int = 20) -> PubMedSearchResult:
        return PubMedSearchResult(query=term, count=1, pmids=("42520140",))

    def fetch(self, pmids: tuple[str, ...]) -> list[PubMedRecord]:
        self.last_pmids = pmids
        return [sample_record()]


class FakeVerifier:
    def verify_pmid(self, value: str) -> VerificationResult:
        return VerificationResult("pmid", value, "valid")

    def verify_doi(self, value: str) -> VerificationResult:
        return VerificationResult("doi", value, "valid", http_status=200)

    def verify_url(self, value: str) -> VerificationResult:
        return VerificationResult("url", value, "valid", http_status=200, resolved_url=value)


class IngestionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "pipeline.db"
        self.engine = create_database(f"sqlite:///{path.as_posix()}")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_persists_verified_candidate_and_state_history(self) -> None:
        summary = IngestionPipeline(
            engine=self.engine,
            pubmed=FakePubMed(),
            verifier=FakeVerifier(),
        ).run(
            IngestionConfig(
                query="probiotics[Title]",
                domain_code="gut_health",
                domain_name_zh="肠道健康",
                domain_description="肠道健康测试定义",
                retmax=1,
            )
        )

        self.assertEqual(summary.status, "PROCESSING")
        self.assertEqual(summary.task_counts, {"VERIFIED": 1})
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Paper)), 1)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(SourceRecord)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ContentSnapshot)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(VerificationRecord)), 3
            )
            task = session.scalar(select(PaperTask))
            self.assertIsNotNone(task)
            self.assertEqual(task.state, "VERIFIED")
            self.assertIsNotNone(task.content_snapshot_id)
            states = list(
                session.scalars(
                    select(TaskStateEvent.to_state)
                    .where(TaskStateEvent.paper_task_id == task.id)
                    .order_by(TaskStateEvent.id)
                )
            )
            self.assertEqual(states, ["DISCOVERED", "NORMALIZED", "VERIFYING", "VERIFIED"])
            run = session.get(PipelineRun, summary.pipeline_run_id)
            self.assertEqual(run.config_snapshot["require_batch_publish_confirmation"], True)
            self.assertEqual(run.config_snapshot["domain_description"], "肠道健康测试定义")
            domain = session.scalar(
                select(ResearchDomain).where(ResearchDomain.code == "gut_health")
            )
            self.assertEqual(domain.description, "肠道健康测试定义")

    def test_empty_search_completes_without_failure(self) -> None:
        class EmptyPubMed(FakePubMed):
            def search(self, term: str, *, retmax: int = 20) -> PubMedSearchResult:
                return PubMedSearchResult(query=term, count=0, pmids=())

            def fetch(self, pmids: tuple[str, ...]) -> list[PubMedRecord]:
                return []

        summary = IngestionPipeline(
            engine=self.engine,
            pubmed=EmptyPubMed(),
            verifier=FakeVerifier(),
        ).run(
            IngestionConfig(
                query="no-results",
                domain_code="gut_health",
                domain_name_zh="肠道健康",
            )
        )
        self.assertEqual(summary.status, "COMPLETED")
        self.assertEqual(summary.task_counts, {})


if __name__ == "__main__":
    unittest.main()
