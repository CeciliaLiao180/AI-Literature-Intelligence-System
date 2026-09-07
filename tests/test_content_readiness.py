from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    ContentSnapshot,
    Paper,
    PaperTask,
    PipelineRun,
    TaskStateEvent,
)
from literature_intelligence.pipeline.content_readiness import ContentReadinessProcessor
from literature_intelligence.services.normalization import compute_sections_sha256


class ContentReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "content.db"
        self.engine = create_database(f"sqlite:///{path.as_posix()}")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _make_task(
        self,
        *,
        include_snapshot: bool = True,
        content_hash: str | None = None,
        rights_basis: str = "public_metadata",
        is_truncated: bool = False,
        truncation_reason: str | None = None,
    ) -> tuple[str, str]:
        with Session(self.engine) as session:
            run = PipelineRun(trigger_type="manual", status="PROCESSING", config_snapshot={})
            paper = Paper(
                canonical_title="Paper",
                normalized_title="paper",
                abstract="Verified abstract.",
            )
            session.add_all([run, paper])
            session.flush()
            snapshot_id = None
            if include_snapshot:
                sections = [
                    {
                        "section_id": "abstract",
                        "name": "Abstract",
                        "text": "Verified abstract.",
                    }
                ]
                snapshot = ContentSnapshot(
                    paper_id=paper.id,
                    source_type="pubmed_abstract",
                    content_scope="abstract",
                    sections_json=sections,
                    content_sha256=content_hash or compute_sections_sha256(sections),
                    source_url="https://pubmed.ncbi.nlm.nih.gov/123/",
                    rights_basis=rights_basis,
                    is_truncated=is_truncated,
                    truncation_reason=truncation_reason,
                )
                session.add(snapshot)
                session.flush()
                snapshot_id = snapshot.id
            task = PaperTask(
                pipeline_run_id=run.id,
                paper_id=paper.id,
                content_snapshot_id=snapshot_id,
                state="VERIFIED",
            )
            session.add(task)
            session.commit()
            return run.id, task.id

    def test_valid_snapshot_becomes_content_ready(self) -> None:
        run_id, task_id = self._make_task()
        summary = ContentReadinessProcessor(engine=self.engine).process(
            pipeline_run_id=run_id
        )
        self.assertEqual(summary.task_counts, {"CONTENT_READY": 1})
        with Session(self.engine) as session:
            states = list(
                session.scalars(
                    select(TaskStateEvent.to_state)
                    .where(TaskStateEvent.paper_task_id == task_id)
                    .order_by(TaskStateEvent.id)
                )
            )
            self.assertEqual(states, ["CONTENT_FETCHING", "CONTENT_READY"])

    def test_missing_snapshot_is_rejected(self) -> None:
        run_id, _ = self._make_task(include_snapshot=False)
        summary = ContentReadinessProcessor(engine=self.engine).process(
            pipeline_run_id=run_id
        )
        self.assertEqual(summary.task_counts, {"REJECTED": 1})

    def test_hash_mismatch_requires_review(self) -> None:
        run_id, _ = self._make_task(content_hash="0" * 64)
        summary = ContentReadinessProcessor(engine=self.engine).process(
            pipeline_run_id=run_id
        )
        self.assertEqual(summary.task_counts, {"REVIEW_PENDING": 1})
        with Session(self.engine) as session:
            self.assertEqual(session.get(PipelineRun, run_id).status, "REVIEW_PENDING")

    def test_disallowed_rights_combination_is_rejected(self) -> None:
        run_id, _ = self._make_task(rights_basis="open_access")
        summary = ContentReadinessProcessor(engine=self.engine).process(
            pipeline_run_id=run_id
        )
        self.assertEqual(summary.task_counts, {"REJECTED": 1})

    def test_truncated_snapshot_requires_reason(self) -> None:
        run_id, _ = self._make_task(is_truncated=True)
        summary = ContentReadinessProcessor(engine=self.engine).process(
            pipeline_run_id=run_id
        )
        self.assertEqual(summary.task_counts, {"REVIEW_PENDING": 1})


if __name__ == "__main__":
    unittest.main()
