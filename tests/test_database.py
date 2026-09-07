from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import PipelineRun, PublicationBatch


EXPECTED_TABLES = {
    "ai_runs",
    "analysis_evidence",
    "authors",
    "content_snapshots",
    "paper_analyses",
    "paper_authors",
    "paper_categories",
    "paper_identifiers",
    "paper_tasks",
    "papers",
    "pipeline_runs",
    "prompt_versions",
    "publication_batches",
    "report_items",
    "research_domains",
    "search_queries",
    "source_records",
    "task_state_events",
    "verification_records",
}


class DatabaseInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "mvp.db"
        self.engine = create_database(f"sqlite:///{db_path.as_posix()}")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_initializes_expected_schema(self) -> None:
        inspector = inspect(self.engine)
        self.assertEqual(set(inspector.get_table_names()), EXPECTED_TABLES)
        task_columns = {column["name"] for column in inspector.get_columns("paper_tasks")}
        self.assertIn("content_snapshot_id", task_columns)
        publication_columns = {
            column["name"] for column in inspector.get_columns("publication_batches")
        }
        self.assertTrue(
            {"preview_sha256", "confirmed_by", "confirmed_at"}.issubset(publication_columns)
        )

    def test_database_rejects_published_batch_without_confirmation(self) -> None:
        with Session(self.engine) as session:
            run = PipelineRun(trigger_type="manual", status="PROCESSING", config_snapshot={})
            session.add(run)
            session.flush()
            session.add(
                PublicationBatch(
                    pipeline_run_id=run.id,
                    edition_date=date(2026, 9, 1),
                    edition_type="daily",
                    title="Test",
                    status="published",
                    preview_sha256="a" * 64,
                    output_path="published/2026-09-01",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
