from __future__ import annotations

import argparse
import json
import urllib.parse
from dataclasses import asdict, dataclass

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from literature_intelligence.config import Settings
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    ContentSnapshot,
    PaperTask,
    PipelineRun,
    TaskStateEvent,
    utcnow,
)
from literature_intelligence.services.normalization import compute_sections_sha256


ALLOWED_CONTENT_POLICIES = {
    ("pubmed_abstract", "abstract", "public_metadata"),
    ("pmc_open_access", "open_access_text", "open_access"),
    ("publisher_excerpt", "excerpt", "licensed_excerpt"),
    ("manual_excerpt", "excerpt", "licensed_excerpt"),
    ("manual_excerpt", "excerpt", "user_provided"),
}


@dataclass(frozen=True)
class ContentCheck:
    decision: str
    reason_code: str | None
    details: dict[str, object]


@dataclass(frozen=True)
class ContentReadinessSummary:
    processed: int
    task_counts: dict[str, int]


class ContentSnapshotValidator:
    def __init__(self, *, max_snapshot_chars: int = 100_000) -> None:
        self.max_snapshot_chars = max_snapshot_chars

    def validate(self, snapshot: ContentSnapshot | None) -> ContentCheck:
        if snapshot is None:
            return ContentCheck(
                decision="reject",
                reason_code="CONTENT_UNAVAILABLE",
                details={"reason": "task_has_no_content_snapshot"},
            )

        policy_key = (
            snapshot.source_type,
            snapshot.content_scope,
            snapshot.rights_basis,
        )
        if policy_key not in ALLOWED_CONTENT_POLICIES:
            return ContentCheck(
                decision="reject",
                reason_code="POLICY_BLOCKED",
                details={
                    "source_type": snapshot.source_type,
                    "content_scope": snapshot.content_scope,
                    "rights_basis": snapshot.rights_basis,
                },
            )

        parsed_url = urllib.parse.urlsplit(snapshot.source_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            return ContentCheck(
                decision="review",
                reason_code="CONTENT_SOURCE_URL_INVALID",
                details={"source_url": snapshot.source_url},
            )

        sections = snapshot.sections_json
        if not isinstance(sections, list) or not sections:
            return ContentCheck(
                decision="reject",
                reason_code="CONTENT_UNAVAILABLE",
                details={"reason": "empty_sections"},
            )

        normalized_sections: list[dict[str, str]] = []
        seen_section_ids: set[str] = set()
        total_chars = 0
        for index, item in enumerate(sections):
            if not isinstance(item, dict):
                return ContentCheck(
                    decision="review",
                    reason_code="CONTENT_STRUCTURE_INVALID",
                    details={"section_index": index, "reason": "section_is_not_an_object"},
                )
            section_id = item.get("section_id")
            name = item.get("name")
            text = item.get("text")
            if not all(isinstance(value, str) and value.strip() for value in (section_id, name, text)):
                return ContentCheck(
                    decision="review",
                    reason_code="CONTENT_STRUCTURE_INVALID",
                    details={"section_index": index, "reason": "missing_required_text"},
                )
            if section_id in seen_section_ids:
                return ContentCheck(
                    decision="review",
                    reason_code="CONTENT_STRUCTURE_INVALID",
                    details={"section_id": section_id, "reason": "duplicate_section_id"},
                )
            seen_section_ids.add(section_id)
            normalized_sections.append(
                {"section_id": section_id, "name": name, "text": text}
            )
            total_chars += len(text)

        if total_chars > self.max_snapshot_chars:
            return ContentCheck(
                decision="review",
                reason_code="CONTENT_TOO_LARGE",
                details={
                    "total_chars": total_chars,
                    "max_snapshot_chars": self.max_snapshot_chars,
                },
            )

        actual_hash = compute_sections_sha256(normalized_sections)
        if actual_hash != snapshot.content_sha256:
            return ContentCheck(
                decision="review",
                reason_code="CONTENT_HASH_MISMATCH",
                details={
                    "expected_sha256": snapshot.content_sha256,
                    "actual_sha256": actual_hash,
                },
            )

        if snapshot.is_truncated and not snapshot.truncation_reason:
            return ContentCheck(
                decision="review",
                reason_code="TRUNCATION_REASON_MISSING",
                details={"is_truncated": True},
            )

        details: dict[str, object] = {
            "content_sha256": snapshot.content_sha256,
            "section_count": len(normalized_sections),
            "total_chars": total_chars,
            "is_truncated": snapshot.is_truncated,
        }
        if snapshot.is_truncated:
            details["warning"] = "CONTENT_TRUNCATED"
            details["truncation_reason"] = snapshot.truncation_reason
        return ContentCheck(decision="ready", reason_code=None, details=details)


class ContentReadinessProcessor:
    def __init__(
        self,
        *,
        engine: Engine,
        validator: ContentSnapshotValidator | None = None,
        actor: str = "content_readiness",
    ) -> None:
        self.engine = engine
        self.validator = validator or ContentSnapshotValidator()
        self.actor = actor
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def process(self, *, pipeline_run_id: str | None = None) -> ContentReadinessSummary:
        with self.sessions.begin() as session:
            statement = select(PaperTask).where(PaperTask.state == "VERIFIED")
            if pipeline_run_id:
                statement = statement.where(PaperTask.pipeline_run_id == pipeline_run_id)
            tasks = list(session.scalars(statement.order_by(PaperTask.created_at)))

            affected_runs: set[str] = set()
            for task in tasks:
                affected_runs.add(task.pipeline_run_id)
                self._transition(session, task, "CONTENT_FETCHING")
                snapshot = (
                    session.get(ContentSnapshot, task.content_snapshot_id)
                    if task.content_snapshot_id
                    else None
                )
                check = self.validator.validate(snapshot)
                if check.decision == "ready":
                    self._transition(
                        session,
                        task,
                        "CONTENT_READY",
                        details=check.details,
                    )
                elif check.decision == "review":
                    self._transition(
                        session,
                        task,
                        "REVIEW_PENDING",
                        reason_code=check.reason_code,
                        details=check.details,
                    )
                else:
                    self._transition(
                        session,
                        task,
                        "REJECTED",
                        reason_code=check.reason_code,
                        details=check.details,
                    )

            for run_id in affected_runs:
                review_count = session.scalar(
                    select(func.count(PaperTask.id)).where(
                        PaperTask.pipeline_run_id == run_id,
                        PaperTask.state == "REVIEW_PENDING",
                    )
                )
                if review_count:
                    run = session.get(PipelineRun, run_id)
                    if run is not None:
                        run.status = "REVIEW_PENDING"

        return ContentReadinessSummary(
            processed=len(tasks),
            task_counts=self._task_counts(pipeline_run_id),
        )

    def _transition(
        self,
        session: Session,
        task: PaperTask,
        to_state: str,
        *,
        reason_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        from_state = task.state
        task.state = to_state
        task.state_changed_at = utcnow()
        session.add(
            TaskStateEvent(
                paper_task_id=task.id,
                from_state=from_state,
                to_state=to_state,
                event_type="transition",
                actor=self.actor,
                reason_code=reason_code,
                details=details or {},
            )
        )

    def _task_counts(self, pipeline_run_id: str | None) -> dict[str, int]:
        with self.sessions() as session:
            statement = select(PaperTask.state, func.count(PaperTask.id)).group_by(
                PaperTask.state
            )
            if pipeline_run_id:
                statement = statement.where(PaperTask.pipeline_run_id == pipeline_run_id)
            return {state: count for state, count in session.execute(statement).all()}


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Validate snapshots and mark content ready.")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--pipeline-run-id")
    args = parser.parse_args()

    engine = create_database(args.database_url)
    summary = ContentReadinessProcessor(engine=engine).process(
        pipeline_run_id=args.pipeline_run_id
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True))
    engine.dispose()


if __name__ == "__main__":
    main()

