from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from literature_intelligence.config import Settings
from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    PaperTask,
    PipelineRun,
    PublicationBatch,
    ReportItem,
    TaskStateEvent,
)
from literature_intelligence.pipeline.site_artifact import validate_site_artifact


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _manifest_sha256(site_dir: Path) -> str:
    return hashlib.sha256(
        (site_dir / "data" / "site-manifest.json").read_bytes()
    ).hexdigest()


def _batch_paper_ids(session: Session, batch_id: str) -> list[int]:
    return list(
        session.scalars(
            select(ReportItem.paper_id)
            .where(ReportItem.publication_batch_id == batch_id)
            .order_by(ReportItem.display_order)
        )
    )


def _batch_tasks(
    session: Session,
    *,
    batch: PublicationBatch,
    paper_ids: list[int],
) -> list[PaperTask]:
    if not paper_ids:
        return []
    return list(
        session.scalars(
            select(PaperTask)
            .where(
                PaperTask.pipeline_run_id == batch.pipeline_run_id,
                PaperTask.paper_id.in_(paper_ids),
            )
            .order_by(PaperTask.id)
        )
    )


def _transition_tasks(
    session: Session,
    *,
    tasks: list[PaperTask],
    expected_state: str,
    target_state: str,
    batch_id: str,
    actor: str,
    reason_code: str,
) -> int:
    created = 0
    for task in tasks:
        if task.state == target_state:
            continue
        if task.state != expected_state:
            raise ValueError(
                f"paper task {task.id} is {task.state}; expected "
                f"{expected_state} or {target_state}"
            )
        changed_at = _utcnow()
        task.state = target_state
        task.state_changed_at = changed_at
        session.add(
            TaskStateEvent(
                paper_task_id=task.id,
                from_state=expected_state,
                to_state=target_state,
                event_type="transition",
                actor=actor,
                reason_code=reason_code,
                details={"batch_id": batch_id},
                created_at=changed_at,
            )
        )
        created += 1
    return created


@dataclass(frozen=True)
class ConfirmationSummary:
    batch_id: str
    status: str
    confirmed_by: str
    confirmed_at: str
    preview_sha256: str
    paper_count: int
    task_events_created: int
    already_confirmed: bool


@dataclass(frozen=True)
class PublicationSummary:
    batch_id: str
    status: str
    published_at: str
    site_url: str
    content_sha256: str
    paper_count: int
    task_events_created: int
    already_published: bool


def confirm_publication_batch(
    *,
    database_url: str,
    batch_id: str,
    preview_site_dir: Path,
    confirmed_by: str,
    confirmation_note: str,
) -> ConfirmationSummary:
    confirmed_by = confirmed_by.strip()
    confirmation_note = confirmation_note.strip()
    if not confirmed_by:
        raise ValueError("confirmed_by must not be empty")
    if not confirmation_note:
        raise ValueError("confirmation_note must not be empty")
    preview_site_dir = preview_site_dir.resolve()
    validate_site_artifact(
        preview_site_dir,
        required_publication_status="preview",
    )
    manifest = json.loads(
        (preview_site_dir / "data" / "site-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if batch_id not in manifest.get("batch_ids", []):
        raise ValueError(f"preview artifact does not contain batch {batch_id}")
    preview_sha256 = _manifest_sha256(preview_site_dir)

    engine = make_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            batch = session.get(PublicationBatch, batch_id)
            if batch is None:
                raise ValueError(f"publication batch does not exist: {batch_id}")
            paper_ids = _batch_paper_ids(session, batch_id)
            if not paper_ids:
                raise ValueError("publication batch has no report items")
            invalid_reviews = list(
                session.scalars(
                    select(ReportItem.review_status).where(
                        ReportItem.publication_batch_id == batch_id,
                        ReportItem.review_status.not_in(("auto_approved", "approved")),
                    )
                )
            )
            if invalid_reviews:
                raise ValueError(
                    f"publication batch has unapproved report items: {invalid_reviews}"
                )
            already_confirmed = batch.status == "approved"
            if batch.status == "published":
                raise ValueError("publication batch is already published")
            if batch.status not in {
                "draft",
                "preview_ready",
                "awaiting_confirmation",
                "approved",
            }:
                raise ValueError(f"publication batch cannot be confirmed from {batch.status}")
            if already_confirmed:
                if (
                    batch.confirmed_by != confirmed_by
                    or batch.confirmation_note != confirmation_note
                    or batch.preview_sha256 != preview_sha256
                    or batch.preview_path != preview_site_dir.as_posix()
                ):
                    raise ValueError("stored publication confirmation conflicts with input")
                assert batch.confirmed_at is not None
                confirmed_at = batch.confirmed_at
                task_events_created = 0
            else:
                tasks = _batch_tasks(
                    session,
                    batch=batch,
                    paper_ids=paper_ids,
                )
                if len(tasks) != len(paper_ids):
                    raise ValueError("publication batch does not map to one task per paper")
                task_events_created = _transition_tasks(
                    session,
                    tasks=tasks,
                    expected_state="READY_FOR_PREVIEW",
                    target_state="PREVIEWED",
                    batch_id=batch_id,
                    actor=confirmed_by,
                    reason_code="BATCH_PREVIEW_CONFIRMED",
                )
                confirmed_at = _utcnow()
                batch.status = "approved"
                batch.preview_path = preview_site_dir.as_posix()
                batch.preview_sha256 = preview_sha256
                batch.confirmed_by = confirmed_by
                batch.confirmed_at = confirmed_at
                batch.confirmation_note = confirmation_note
        return ConfirmationSummary(
            batch_id=batch_id,
            status="approved",
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at.isoformat(),
            preview_sha256=preview_sha256,
            paper_count=len(paper_ids),
            task_events_created=task_events_created,
            already_confirmed=already_confirmed,
        )
    finally:
        engine.dispose()


def finalize_publication_batch(
    *,
    database_url: str,
    batch_id: str,
    published_site_dir: Path,
    site_url: str,
) -> PublicationSummary:
    published_site_dir = published_site_dir.resolve()
    parsed_url = urlparse(site_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("site_url must be an absolute HTTPS URL")
    normalized_site_url = site_url.rstrip("/") + "/"
    validate_site_artifact(
        published_site_dir,
        required_publication_status="published",
    )
    manifest = json.loads(
        (published_site_dir / "data" / "site-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if batch_id not in manifest.get("batch_ids", []):
        raise ValueError(f"published artifact does not contain batch {batch_id}")
    content_sha256 = _manifest_sha256(published_site_dir)

    engine = make_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            batch = session.get(PublicationBatch, batch_id)
            if batch is None:
                raise ValueError(f"publication batch does not exist: {batch_id}")
            paper_ids = _batch_paper_ids(session, batch_id)
            already_published = batch.status == "published"
            if already_published:
                if (
                    batch.output_path != normalized_site_url
                    or batch.content_sha256 != content_sha256
                ):
                    raise ValueError("stored publication result conflicts with input")
                assert batch.published_at is not None
                published_at = batch.published_at
                task_events_created = 0
            else:
                if batch.status != "approved":
                    raise ValueError(
                        f"publication batch is {batch.status}; expected approved"
                    )
                if (
                    not batch.confirmed_by
                    or batch.confirmed_at is None
                    or not batch.preview_sha256
                ):
                    raise ValueError("publication batch has no complete human confirmation")
                tasks = _batch_tasks(
                    session,
                    batch=batch,
                    paper_ids=paper_ids,
                )
                task_events_created = _transition_tasks(
                    session,
                    tasks=tasks,
                    expected_state="PREVIEWED",
                    target_state="PUBLISHED",
                    batch_id=batch_id,
                    actor="github_pages_publish",
                    reason_code="GITHUB_PAGES_DEPLOYED",
                )
                published_at = _utcnow()
                batch.output_path = normalized_site_url
                batch.content_sha256 = content_sha256
                batch.published_at = published_at
                batch.status = "published"
                pipeline_run = session.get(PipelineRun, batch.pipeline_run_id)
                if pipeline_run is not None:
                    pipeline_run.status = "COMPLETED"
                    pipeline_run.finished_at = published_at
                    pipeline_run.published_count = len(paper_ids)
        return PublicationSummary(
            batch_id=batch_id,
            status="published",
            published_at=published_at.isoformat(),
            site_url=normalized_site_url,
            content_sha256=content_sha256,
            paper_count=len(paper_ids),
            task_events_created=task_events_created,
            already_published=already_published,
        )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Confirm or finalize a batch-level human-gated publication."
    )
    parser.add_argument("--database-url", default=Settings.from_env().database_url)
    subparsers = parser.add_subparsers(dest="command", required=True)

    confirm_parser = subparsers.add_parser("confirm")
    confirm_parser.add_argument("--batch-id", required=True)
    confirm_parser.add_argument("--preview-site-dir", type=Path, default=Path("site"))
    confirm_parser.add_argument("--confirmed-by", required=True)
    confirm_parser.add_argument("--confirmation-note", required=True)

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--batch-id", required=True)
    finalize_parser.add_argument(
        "--published-site-dir", type=Path, default=Path("site")
    )
    finalize_parser.add_argument("--site-url", required=True)
    args = parser.parse_args()

    if args.command == "confirm":
        result = confirm_publication_batch(
            database_url=args.database_url,
            batch_id=args.batch_id,
            preview_site_dir=args.preview_site_dir,
            confirmed_by=args.confirmed_by,
            confirmation_note=args.confirmation_note,
        )
    else:
        result = finalize_publication_batch(
            database_url=args.database_url,
            batch_id=args.batch_id,
            published_site_dir=args.published_site_dir,
            site_url=args.site_url,
        )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
