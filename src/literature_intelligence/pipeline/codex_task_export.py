from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, exists, select
from sqlalchemy.orm import Session

from literature_intelligence.config import Settings
from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    Author,
    ContentSnapshot,
    Paper,
    PaperAuthor,
    PaperIdentifier,
    PaperTask,
    ResearchDomain,
    SearchQuery,
    SourceRecord,
    VerificationRecord,
)
from literature_intelligence.pipeline.content_readiness import ContentSnapshotValidator


PACKAGE_VERSION = "1.0"
INPUT_CONTRACT_VERSION = "1.0"
PROMPT_WORKFLOW = (
    "paper_relevance",
    "paper_analysis",
    "paper_classification",
    "report_composition",
    "report_quality_gate",
)


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date_or_none(value: date | None) -> str | None:
    return value.isoformat() if value else None


class CodexTaskPackageExporter:
    """Build a read-only handoff package for Codex from CONTENT_READY tasks."""

    def __init__(
        self,
        *,
        engine: Engine,
        prompt_spec_path: Path,
        snapshot_validator: ContentSnapshotValidator | None = None,
    ) -> None:
        self.engine = engine
        self.prompt_spec_path = prompt_spec_path.resolve()
        self.snapshot_validator = snapshot_validator or ContentSnapshotValidator()

    def build(
        self,
        *,
        batch_id: str = "manual_export",
        domain_code: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        if not self.prompt_spec_path.is_file():
            raise FileNotFoundError(f"Prompt spec not found: {self.prompt_spec_path}")

        with Session(self.engine) as session:
            statement = select(PaperTask).where(PaperTask.state == "CONTENT_READY")
            if pipeline_run_id:
                statement = statement.where(PaperTask.pipeline_run_id == pipeline_run_id)
            if domain_code:
                domain_match = (
                    select(SourceRecord.id)
                    .join(SearchQuery, SearchQuery.id == SourceRecord.search_query_id)
                    .join(ResearchDomain, ResearchDomain.id == SearchQuery.domain_id)
                    .where(
                        SourceRecord.pipeline_run_id == PaperTask.pipeline_run_id,
                        SourceRecord.paper_id == PaperTask.paper_id,
                        ResearchDomain.code == domain_code,
                    )
                )
                statement = statement.where(exists(domain_match))
            statement = statement.order_by(PaperTask.created_at, PaperTask.id)
            if limit is not None:
                statement = statement.limit(limit)
            paper_tasks = list(session.scalars(statement))

            active_domains = self._active_domains(session)
            tasks = [self._build_task(session, task, active_domains) for task in paper_tasks]

        selected_domain = next(
            (domain for domain in active_domains if domain["code"] == domain_code),
            None,
        )
        pipeline_run_ids = list(dict.fromkeys(task["pipeline_run_id"] for task in tasks))
        generated_at = _iso_datetime(datetime.now(timezone.utc))

        return {
            "package_version": PACKAGE_VERSION,
            "generated_at": generated_at,
            "batch": {
                "batch_id": batch_id,
                "source": "pubmed",
                "required_state": "CONTENT_READY",
                "pipeline_run_ids": pipeline_run_ids,
                "task_count": len(tasks),
                "generated_at": generated_at,
            },
            "domain": selected_domain,
            "selection": {
                "required_state": "CONTENT_READY",
                "domain_code": domain_code,
                "pipeline_run_id": pipeline_run_id,
                "limit": limit,
            },
            "prompt_spec": {
                "path": self.prompt_spec_path.as_posix(),
                "sha256": _file_sha256(self.prompt_spec_path),
                "input_contract_version": INPUT_CONTRACT_VERSION,
                "workflow": list(PROMPT_WORKFLOW),
            },
            "execution_mode": "codex_manual",
            "state_policy": {
                "database_is_read_only_for_export": True,
                "export_does_not_advance_task_state": True,
                "formal_publish_requires_human_confirmation": True,
            },
            "task_count": len(tasks),
            "tasks": tasks,
        }

    def write(
        self,
        output_path: Path,
        *,
        batch_id: str = "manual_export",
        domain_code: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        package = self.build(
            batch_id=batch_id,
            domain_code=domain_code,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return package

    def _build_task(
        self,
        session: Session,
        task: PaperTask,
        active_domains: list[dict[str, Any]],
    ) -> dict[str, Any]:
        paper = session.get(Paper, task.paper_id)
        snapshot = (
            session.get(ContentSnapshot, task.content_snapshot_id)
            if task.content_snapshot_id
            else None
        )
        if paper is None:
            raise ValueError(f"Task {task.id} refers to a missing paper")

        content_check = self.snapshot_validator.validate(snapshot)
        if content_check.decision != "ready" or snapshot is None:
            raise ValueError(
                f"Task {task.id} has an invalid CONTENT_READY snapshot: "
                f"{content_check.reason_code or content_check.decision}"
            )
        if snapshot.paper_id != paper.id:
            raise ValueError(f"Task {task.id} snapshot belongs to a different paper")

        identifiers = dict(
            session.execute(
                select(PaperIdentifier.scheme, PaperIdentifier.value)
                .where(PaperIdentifier.paper_id == paper.id)
                .order_by(PaperIdentifier.is_primary.desc(), PaperIdentifier.id)
            ).all()
        )
        authors = [
            {
                "name": author.display_name,
                "orcid": author.orcid,
                "affiliation": paper_author.affiliation,
            }
            for paper_author, author in session.execute(
                select(PaperAuthor, Author)
                .join(Author, Author.id == PaperAuthor.author_id)
                .where(PaperAuthor.paper_id == paper.id)
                .order_by(PaperAuthor.author_order)
            ).all()
        ]
        source_domain_codes = list(
            session.scalars(
                select(ResearchDomain.code)
                .join(SearchQuery, SearchQuery.domain_id == ResearchDomain.id)
                .join(SourceRecord, SourceRecord.search_query_id == SearchQuery.id)
                .where(
                    SourceRecord.paper_id == paper.id,
                    SourceRecord.pipeline_run_id == task.pipeline_run_id,
                )
                .distinct()
                .order_by(ResearchDomain.code)
            )
        )
        verification, verified_source_links = self._verification_data(
            session, task, paper, identifiers
        )
        publication_date_metadata = self._publication_date_metadata(
            session, task, paper
        )

        input_data: dict[str, Any] = {
            "contract_version": INPUT_CONTRACT_VERSION,
            "task": "paper_relevance",
            "paper": {
                "paper_id": paper.id,
                "title": paper.canonical_title,
                "abstract": paper.abstract,
                "journal": paper.journal_name,
                "publication_date": _date_or_none(paper.publication_date),
                "identifiers": identifiers,
                "study_metadata": {
                    "authors": authors,
                    "article_type": paper.article_type,
                    "language": paper.language,
                    "publication_date_precision": paper.publication_date_precision,
                    "publication_date_metadata": publication_date_metadata,
                    "source_domain_codes": source_domain_codes,
                },
            },
            "content_snapshot": {
                "snapshot_id": snapshot.id,
                "source_type": snapshot.source_type,
                "content_scope": snapshot.content_scope,
                "source_url": snapshot.source_url,
                "rights_basis": snapshot.rights_basis,
                "retrieved_at": _iso_datetime(snapshot.retrieved_at),
                "content_sha256": snapshot.content_sha256,
                "is_truncated": snapshot.is_truncated,
                "truncation_reason": snapshot.truncation_reason,
                "sections": snapshot.sections_json,
            },
            "domain_context": {
                "target_audience": "保健食品、生物科技与生命科学研发人员",
                "active_domains": active_domains,
            },
            "verified_source_links": verified_source_links,
            "verification_summary": verification,
            "runtime_rules": {
                "output_language": "zh-CN",
                "max_key_findings": 5,
                "truncated": snapshot.is_truncated,
                "manual_human_confirmation_required_before_publish": True,
            },
        }
        return {
            "paper_task_id": task.id,
            "pipeline_run_id": task.pipeline_run_id,
            "current_state": task.state,
            "input_sha256": _canonical_sha256(input_data),
            "input_data": input_data,
            "codex_workflow": [
                {
                    "order": 1,
                    "prompt_key": "paper_relevance",
                    "input": "input_data",
                    "produces": "paper_relevance",
                },
                {
                    "order": 2,
                    "prompt_key": "paper_analysis",
                    "input": "input_data",
                    "run_when": "paper_relevance.decision == 'relevant'",
                    "produces": "paper_analysis",
                },
                {
                    "order": 3,
                    "prompt_key": "paper_classification",
                    "input": "input_data + paper_analysis",
                    "run_when": "paper_relevance.decision == 'relevant'",
                    "produces": "paper_classification",
                },
                {
                    "order": 4,
                    "prompt_key": "report_composition",
                    "input": "input_data + paper_analysis + paper_classification",
                    "run_when": "paper_relevance.decision == 'relevant'",
                    "produces": "report_composition",
                },
                {
                    "order": 5,
                    "prompt_key": "report_quality_gate",
                    "input": "input_data + paper_analysis + paper_classification + report_composition",
                    "run_when": "paper_relevance.decision == 'relevant'",
                    "produces": "report_quality_gate",
                },
            ],
        }

    @staticmethod
    def _active_domains(session: Session) -> list[dict[str, Any]]:
        return [
            {
                "code": domain.code,
                "name_zh": domain.name_zh,
                "description": domain.description,
            }
            for domain in session.scalars(
                select(ResearchDomain)
                .where(ResearchDomain.is_active.is_(True))
                .order_by(ResearchDomain.code)
            )
        ]

    @staticmethod
    def _verification_data(
        session: Session,
        task: PaperTask,
        paper: Paper,
        identifiers: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        latest: dict[tuple[str, str], VerificationRecord] = {}
        records = session.scalars(
            select(VerificationRecord)
            .where(VerificationRecord.paper_task_id == task.id)
            .order_by(VerificationRecord.checked_at, VerificationRecord.id)
        )
        for record in records:
            latest[(record.check_type, record.target)] = record

        verification = [
            {
                "check_type": record.check_type,
                "target": record.target,
                "status": record.status,
                "http_status": record.http_status,
                "resolved_url": record.resolved_url,
                "checked_at": _iso_datetime(record.checked_at),
            }
            for record in latest.values()
        ]

        links: list[str] = []
        valid_url_records = [
            record
            for record in latest.values()
            if record.check_type == "url" and record.status == "valid"
        ]
        if paper.pubmed_url and any(
            record.target == paper.pubmed_url or record.resolved_url == paper.pubmed_url
            for record in valid_url_records
        ):
            links.append(paper.pubmed_url)

        doi = identifiers.get("doi")
        if doi and any(
            record.check_type == "doi"
            and record.status == "valid"
            and record.target.casefold() == doi.casefold()
            for record in latest.values()
        ):
            links.append(f"https://doi.org/{doi}")
        return verification, links

    @staticmethod
    def _publication_date_metadata(
        session: Session,
        task: PaperTask,
        paper: Paper,
    ) -> dict[str, Any]:
        source_record = session.scalar(
            select(SourceRecord)
            .where(
                SourceRecord.pipeline_run_id == task.pipeline_run_id,
                SourceRecord.paper_id == paper.id,
                SourceRecord.source == "pubmed",
            )
            .order_by(SourceRecord.fetched_at.desc(), SourceRecord.id.desc())
            .limit(1)
        )
        payload = source_record.raw_payload if source_record else {}
        is_future = bool(
            paper.publication_date
            and paper.publication_date > datetime.now(timezone.utc).date()
        )
        warnings: list[dict[str, str]] = []
        if is_future:
            warnings.append(
                {
                    "code": "FUTURE_PUBLICATION_DATE",
                    "message": (
                        "归一化日期晚于任务包生成日期；该值来自 PubMed "
                        "JournalIssue/PubDate，已原样保留。应结合 PubModel、"
                        "ArticleDate 和 History 判断是否为在线优先或未来期次元数据。"
                    ),
                }
            )
        return {
            "normalized_date": _date_or_none(paper.publication_date),
            "precision": paper.publication_date_precision,
            "source_path": payload.get(
                "publication_date_source",
                "Article/Journal/JournalIssue/PubDate",
            ),
            "raw_issue_date": {
                "year": payload.get("publication_year"),
                "month": payload.get("publication_month"),
                "day": payload.get("publication_day"),
                "medline_date": payload.get("medline_date"),
            },
            "publication_model": payload.get("publication_model"),
            "citation_status": payload.get("citation_status"),
            "article_dates": payload.get("article_dates", []),
            "history_dates": payload.get("history_dates", []),
            "is_future_date": is_future,
            "warnings": warnings,
        }


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Export CONTENT_READY papers as a read-only Codex task package."
    )
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--batch-id")
    parser.add_argument("--domain-code")
    parser.add_argument("--pipeline-run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prompt-spec", type=Path, default=Path("config/prompts.v1.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    engine = make_engine(args.database_url)
    try:
        package = CodexTaskPackageExporter(
            engine=engine,
            prompt_spec_path=args.prompt_spec,
        ).write(
            args.output,
            batch_id=args.batch_id or args.output.stem,
            domain_code=args.domain_code,
            pipeline_run_id=args.pipeline_run_id,
            limit=args.limit,
        )
        print(
            json.dumps(
                {
                    "output": args.output.resolve().as_posix(),
                    "task_count": package["task_count"],
                    "required_state": "CONTENT_READY",
                    "database_mutated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
