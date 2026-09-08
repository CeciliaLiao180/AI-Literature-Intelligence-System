from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    PaperTask,
    PipelineRun,
    PublicationBatch,
    ReportItem,
    TaskStateEvent,
)
from literature_intelligence.pipeline.publication_release import (
    confirm_publication_batch,
    finalize_publication_batch,
)
from literature_intelligence.pipeline.site_artifact import validate_site_artifact
from literature_intelligence.pipeline.static_site import (
    build_cumulative_site_data,
    generate_site,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BATCH_ID = "b031d6db-5d87-4506-96c6-5364ad63606a"


class PublicationReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "release.db"
        shutil.copy2(PROJECT_ROOT / "data" / "gut_health_mvp.db", self.database_path)
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session, session.begin():
                batch = session.get(PublicationBatch, BATCH_ID)
                assert batch is not None
                batch.status = "draft"
                batch.preview_path = None
                batch.preview_sha256 = None
                batch.output_path = None
                batch.content_sha256 = None
                batch.confirmed_by = None
                batch.confirmed_at = None
                batch.confirmation_note = None
                batch.published_at = None
                paper_ids = list(
                    session.scalars(
                        select(ReportItem.paper_id).where(
                            ReportItem.publication_batch_id == BATCH_ID
                        )
                    )
                )
                tasks = session.scalars(
                    select(PaperTask).where(
                        PaperTask.pipeline_run_id == batch.pipeline_run_id,
                        PaperTask.paper_id.in_(paper_ids),
                    )
                )
                for task in tasks:
                    task.state = "READY_FOR_PREVIEW"
                session.execute(
                    delete(TaskStateEvent).where(
                        TaskStateEvent.reason_code.in_(
                            ("BATCH_PREVIEW_CONFIRMED", "GITHUB_PAGES_DEPLOYED")
                        )
                    )
                )
        finally:
            engine.dispose()
        self.preview_dir = self.root / "preview"
        preview_data = build_cumulative_site_data(
            database_url=self.database_url,
            site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
            journal_metrics_path=PROJECT_ROOT / "config" / "journal_metrics.v1.json",
        )
        generate_site(preview_data, self.preview_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_confirm_generate_publish_and_finalize_are_audited(self) -> None:
        confirmation = confirm_publication_batch(
            database_url=self.database_url,
            batch_id=BATCH_ID,
            preview_site_dir=self.preview_dir,
            confirmed_by="CeciliaLiao180",
            confirmation_note="批准首次正式发布",
        )
        self.assertEqual(confirmation.status, "approved")
        self.assertEqual(confirmation.paper_count, 4)
        self.assertEqual(confirmation.task_events_created, 4)
        self.assertFalse(confirmation.already_confirmed)

        repeated = confirm_publication_batch(
            database_url=self.database_url,
            batch_id=BATCH_ID,
            preview_site_dir=self.preview_dir,
            confirmed_by="CeciliaLiao180",
            confirmation_note="批准首次正式发布",
        )
        self.assertTrue(repeated.already_confirmed)
        self.assertEqual(repeated.task_events_created, 0)

        published_data = build_cumulative_site_data(
            database_url=self.database_url,
            site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
            journal_metrics_path=PROJECT_ROOT / "config" / "journal_metrics.v1.json",
            publication_status="published",
        )
        published_dir = self.root / "published"
        generate_site(published_data, published_dir)
        validation = validate_site_artifact(
            published_dir,
            required_publication_status="published",
        )
        self.assertEqual(validation["papers"], 4)
        home = (published_dir / "index.html").read_text(encoding="utf-8")
        detail = (published_dir / "papers" / "4.html").read_text(encoding="utf-8")
        self.assertIn('name="robots" content="index,follow"', home)
        self.assertNotIn("AI Research Digest · Preview", home)
        self.assertIn("本批次已完成人工确认并正式发布", detail)

        publication = finalize_publication_batch(
            database_url=self.database_url,
            batch_id=BATCH_ID,
            published_site_dir=published_dir,
            site_url=(
                "https://cecilialiao180.github.io/"
                "AI-Literature-Intelligence-System/"
            ),
        )
        self.assertEqual(publication.status, "published")
        self.assertEqual(publication.task_events_created, 4)
        self.assertFalse(publication.already_published)

        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session:
                batch = session.get(PublicationBatch, BATCH_ID)
                assert batch is not None
                self.assertEqual(batch.status, "published")
                self.assertEqual(batch.confirmed_by, "CeciliaLiao180")
                self.assertIsNotNone(batch.published_at)
                self.assertEqual(
                    batch.output_path,
                    "https://cecilialiao180.github.io/AI-Literature-Intelligence-System/",
                )
                state_counts = dict(
                    session.execute(
                        select(PaperTask.state, func.count(PaperTask.id)).group_by(
                            PaperTask.state
                        )
                    ).all()
                )
                self.assertEqual(state_counts["PUBLISHED"], 4)
                event_count = session.scalar(
                    select(func.count(TaskStateEvent.id)).where(
                        TaskStateEvent.reason_code.in_(
                            ("BATCH_PREVIEW_CONFIRMED", "GITHUB_PAGES_DEPLOYED")
                        )
                    )
                )
                self.assertEqual(event_count, 8)
                pipeline_run = session.get(PipelineRun, batch.pipeline_run_id)
                assert pipeline_run is not None
                self.assertEqual(pipeline_run.status, "COMPLETED")
                self.assertEqual(pipeline_run.published_count, 4)
        finally:
            engine.dispose()

    def test_published_site_rejects_unconfirmed_batch(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved publication batch"):
            build_cumulative_site_data(
                database_url=self.database_url,
                site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
                journal_metrics_path=PROJECT_ROOT
                / "config"
                / "journal_metrics.v1.json",
                publication_status="published",
            )

    def test_finalize_rejects_preview_artifact_before_database_write(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 'published'"):
            finalize_publication_batch(
                database_url=self.database_url,
                batch_id=BATCH_ID,
                published_site_dir=self.preview_dir,
                site_url=(
                    "https://cecilialiao180.github.io/"
                    "AI-Literature-Intelligence-System/"
                ),
            )
        engine = make_engine(self.database_url)
        try:
            with Session(engine) as session:
                batch = session.get(PublicationBatch, BATCH_ID)
                assert batch is not None
                self.assertEqual(batch.status, "draft")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
