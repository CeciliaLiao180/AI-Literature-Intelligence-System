from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    AIRun,
    AnalysisEvidence,
    PaperAnalysis,
    PaperCategory,
    PaperTask,
    PipelineRun,
    PromptVersion,
    PublicationBatch,
    ReportItem,
)
from literature_intelligence.pipeline.codex_results_import import (
    CodexImportError,
    import_codex_results,
)


BATCH_ID = "b031d6db-5d87-4506-96c6-5364ad63606a"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CodexResultsImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "import.db"
        shutil.copy2(PROJECT_ROOT / "data" / "gut_health_mvp.db", self.database_path)
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session, session.begin():
                for model in (
                    ReportItem,
                    PublicationBatch,
                    AnalysisEvidence,
                    PaperCategory,
                    PaperAnalysis,
                    AIRun,
                    PromptVersion,
                ):
                    session.execute(delete(model))
                for task in session.scalars(select(PaperTask)):
                    task.state = "CONTENT_READY"
                    task.relevance_score = None
                for run in session.scalars(select(PipelineRun)):
                    snapshot = dict(run.config_snapshot or {})
                    snapshot.pop("codex_result_import", None)
                    run.config_snapshot = snapshot
                    run.status = "PROCESSING"
        finally:
            engine.dispose()
        self.task_package = (
            PROJECT_ROOT / "data" / "codex_tasks" / f"{BATCH_ID}_gut_health.json"
        )
        self.screening_results = (
            PROJECT_ROOT
            / "data"
            / "codex_results"
            / f"{BATCH_ID}_gut_health_screening.json"
        )
        self.mvp_results = (
            PROJECT_ROOT
            / "data"
            / "codex_results"
            / f"{BATCH_ID}_gut_health_mvp.json"
        )
        self.prompt_spec = PROJECT_ROOT / "config" / "prompts.v1.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _import(self):
        return import_codex_results(
            database_url=self.database_url,
            task_package_path=self.task_package,
            screening_results_path=self.screening_results,
            mvp_results_path=self.mvp_results,
            prompt_spec_path=self.prompt_spec,
            edition_date=date(2026, 9, 7),
        )

    def test_persists_validated_codex_results_and_state_history(self) -> None:
        summary = self._import()
        self.assertEqual(summary.prompt_versions_created, 5)
        self.assertEqual(summary.ai_runs_created, 23)
        self.assertEqual(summary.analyses_created, 4)
        self.assertGreater(summary.evidence_created, 0)
        self.assertEqual(summary.categories_created, 4)
        self.assertEqual(summary.report_items_created, 4)
        self.assertEqual(
            summary.task_counts,
            {"IRRELEVANT": 2, "READY_FOR_PREVIEW": 4, "REVIEW_PENDING": 1},
        )
        self.assertEqual(summary.batch_status, "draft")

        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session:
                self.assertEqual(session.scalar(select(func.count(PromptVersion.id))), 5)
                self.assertEqual(session.scalar(select(func.count(AIRun.id))), 23)
                self.assertEqual(session.scalar(select(func.count(PaperAnalysis.id))), 4)
                self.assertGreater(
                    session.scalar(select(func.count(AnalysisEvidence.id))), 0
                )
                self.assertEqual(session.scalar(select(func.count(PaperCategory.paper_id))), 4)
                self.assertEqual(session.scalar(select(func.count(ReportItem.id))), 4)
                batch = session.get(PublicationBatch, BATCH_ID)
                self.assertIsNotNone(batch)
                assert batch is not None
                self.assertEqual(batch.status, "draft")
                self.assertIsNone(batch.confirmed_at)
                self.assertIsNone(batch.published_at)
                self.assertTrue(
                    all(
                        value == "auto_approved"
                        for value in session.scalars(select(ReportItem.review_status))
                    )
                )
                run = session.get(PipelineRun, BATCH_ID)
                self.assertIsNotNone(run)
                assert run is not None
                self.assertIn("codex_result_import", run.config_snapshot)
        finally:
            engine.dispose()

    def test_reimport_is_idempotent(self) -> None:
        first = self._import()
        second = self._import()
        self.assertEqual(first.ai_runs_created, 23)
        self.assertEqual(second.prompt_versions_created, 0)
        self.assertEqual(second.ai_runs_created, 0)
        self.assertEqual(second.analyses_created, 0)
        self.assertEqual(second.evidence_created, 0)
        self.assertEqual(second.categories_created, 0)
        self.assertEqual(second.report_items_created, 0)
        self.assertEqual(second.state_events_created, 0)

    def test_prompt_conflict_rolls_back_entire_import(self) -> None:
        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session, session.begin():
                session.add(
                    PromptVersion(
                        prompt_key="paper_analysis",
                        version="1.0.0",
                        template_sha256="f" * 64,
                        template_snapshot={"conflict": True},
                        schema_version="1.0",
                        status="draft",
                    )
                )
            with self.assertRaises(CodexImportError):
                self._import()
            with Session(engine) as session:
                self.assertEqual(session.scalar(select(func.count(AIRun.id))), 0)
                self.assertEqual(session.scalar(select(func.count(PaperAnalysis.id))), 0)
                self.assertEqual(session.scalar(select(func.count(PublicationBatch.id))), 0)
                self.assertEqual(session.scalar(select(func.count(PromptVersion.id))), 1)
                states = set(session.scalars(select(PaperTask.state)))
                self.assertEqual(states, {"CONTENT_READY"})
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
