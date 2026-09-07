from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.config import Settings
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
    ResearchDomain,
    TaskStateEvent,
    utcnow,
)
from literature_intelligence.pipeline.codex_mvp_preview import validate_mvp_results
from literature_intelligence.pipeline.codex_screening import file_sha256


PROMPT_KEYS = (
    "paper_relevance",
    "paper_analysis",
    "paper_classification",
    "report_composition",
    "report_quality_gate",
)


class CodexImportError(ValueError):
    pass


@dataclass(frozen=True)
class CodexImportSummary:
    batch_id: str
    edition_date: str
    batch_status: str
    prompt_versions_created: int
    ai_runs_created: int
    analyses_created: int
    evidence_created: int
    categories_created: int
    report_items_created: int
    state_events_created: int
    task_counts: dict[str, int]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ensure_prompt_versions(
    session: Session,
    *,
    prompt_spec_path: Path,
    prompt_spec_sha256: str,
    prompt_versions: dict[str, str],
) -> tuple[dict[str, PromptVersion], int]:
    actual_sha256 = file_sha256(prompt_spec_path)
    if actual_sha256 != prompt_spec_sha256:
        raise CodexImportError("prompt specification SHA-256 mismatch")
    missing = set(PROMPT_KEYS) - set(prompt_versions)
    extra = set(prompt_versions) - set(PROMPT_KEYS)
    if missing or extra:
        raise CodexImportError(
            f"prompt version map mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )

    prompt_text = prompt_spec_path.read_text(encoding="utf-8")
    rows: dict[str, PromptVersion] = {}
    created = 0
    for key in PROMPT_KEYS:
        version = prompt_versions[key]
        snapshot = {
            "prompt_key": key,
            "version": version,
            "source_name": prompt_spec_path.name,
            "source_sha256": actual_sha256,
            "source_text": prompt_text,
        }
        template_sha256 = _canonical_hash(snapshot)
        row = session.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_key == key,
                PromptVersion.version == version,
            )
        )
        if row is None:
            row = PromptVersion(
                prompt_key=key,
                version=version,
                template_sha256=template_sha256,
                template_snapshot=snapshot,
                schema_version="1.0",
                status="draft",
            )
            session.add(row)
            session.flush()
            created += 1
        elif row.template_sha256 != template_sha256 or row.template_snapshot != snapshot:
            raise CodexImportError(f"stored prompt snapshot conflicts with {key}@{version}")
        rows[key] = row
    return rows, created


def _ensure_ai_run(
    session: Session,
    *,
    paper_task_id: str,
    prompt: PromptVersion,
    input_sha256: str,
    output: dict[str, Any],
    execution_mode: str,
    finished_at: datetime,
) -> tuple[AIRun, int]:
    idempotency_key = hashlib.sha256(
        f"{paper_task_id}|{prompt.prompt_key}|{prompt.version}|{input_sha256}".encode()
    ).hexdigest()
    row = session.scalar(
        select(AIRun).where(
            AIRun.idempotency_key == idempotency_key,
            AIRun.attempt_no == 1,
        )
    )
    if row is None:
        row = AIRun(
            paper_task_id=paper_task_id,
            step=prompt.prompt_key,
            prompt_version_id=prompt.id,
            provider="codex",
            model=execution_mode,
            idempotency_key=idempotency_key,
            attempt_no=1,
            input_sha256=input_sha256,
            status="succeeded",
            output_json=output,
            finished_at=finished_at,
        )
        session.add(row)
        session.flush()
        return row, 1
    if (
        row.prompt_version_id != prompt.id
        or row.input_sha256 != input_sha256
        or row.output_json != output
        or row.status != "succeeded"
    ):
        raise CodexImportError(
            f"stored Codex run conflicts with {paper_task_id}/{prompt.prompt_key}"
        )
    return row, 0


def _ensure_analysis(
    session: Session,
    *,
    paper_id: int,
    ai_run: AIRun,
    output: dict[str, Any],
    task: dict[str, Any],
) -> tuple[PaperAnalysis, int, int]:
    expected_reasons = output["warnings"] if output["needs_human_review"] else []
    row = session.scalar(
        select(PaperAnalysis).where(
            PaperAnalysis.paper_id == paper_id,
            PaperAnalysis.version == 1,
        )
    )
    if row is not None:
        matches = (
            row.ai_run_id == ai_run.id
            and row.study_type == output["study_type"]
            and row.background == output["background"]
            and row.objective == output["objective"]
            and row.methods_json == output["methods"]
            and row.sample_json == output["sample"]
            and row.key_findings_json == output["key_findings"]
            and row.limitations_json == output["limitations"]
            and row.rd_implications_json == output["rd_implications"]
            and row.confidence == Decimal(str(output["confidence"]))
            and row.needs_human_review == output["needs_human_review"]
            and row.review_reasons_json == expected_reasons
        )
        if not matches:
            raise CodexImportError(f"stored analysis conflicts with paper {paper_id}")
        return row, 0, 0

    row = PaperAnalysis(
        paper_id=paper_id,
        ai_run_id=ai_run.id,
        version=1,
        study_type=output["study_type"],
        background=output["background"],
        objective=output["objective"],
        methods_json=output["methods"],
        sample_json=output["sample"],
        key_findings_json=output["key_findings"],
        limitations_json=output["limitations"],
        rd_implications_json=output["rd_implications"],
        confidence=Decimal(str(output["confidence"])),
        needs_human_review=output["needs_human_review"],
        review_reasons_json=expected_reasons,
        is_current=True,
    )
    session.add(row)
    session.flush()

    sections = {
        section["section_id"]: section["text"]
        for section in task["input_data"]["content_snapshot"]["sections"]
    }
    evidence_count = 0
    for finding in output["key_findings"]:
        for evidence in finding["evidence"]:
            quote = evidence["quote"]
            start = sections[evidence["section_id"]].find(quote)
            session.add(
                AnalysisEvidence(
                    analysis_id=row.id,
                    claim_key=finding["finding_id"],
                    source_section=evidence["section_id"],
                    quote_text=quote,
                    char_start=start,
                    char_end=start + len(quote),
                    evidence_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                    support_level=evidence["support_level"],
                )
            )
            evidence_count += 1
    return row, 1, evidence_count


def _ensure_categories(
    session: Session,
    *,
    paper_id: int,
    classification: dict[str, Any],
) -> int:
    created = 0
    for item in classification["classifications"]:
        domain = session.scalar(
            select(ResearchDomain).where(ResearchDomain.code == item["domain_code"])
        )
        if domain is None:
            raise CodexImportError(f"database has no domain {item['domain_code']}")
        score = Decimal(str(item["score"]))
        row = session.get(PaperCategory, (paper_id, domain.id))
        if row is None:
            session.add(
                PaperCategory(
                    paper_id=paper_id,
                    domain_id=domain.id,
                    score=score,
                    reason=item["reason"],
                    source="codex_manual",
                )
            )
            created += 1
        elif row.score != score or row.reason != item["reason"] or row.source != "codex_manual":
            raise CodexImportError(
                f"stored classification conflicts with paper {paper_id}/{item['domain_code']}"
            )
    return created


def _transition_task(
    session: Session,
    *,
    task: PaperTask,
    states: list[str],
    batch_id: str,
    decision: str,
) -> int:
    target = states[-1]
    if task.state == target:
        return 0
    if task.state != "CONTENT_READY":
        raise CodexImportError(
            f"task {task.id} is {task.state}; expected CONTENT_READY or {target}"
        )
    created = 0
    for state in states:
        previous = task.state
        task.state = state
        task.state_changed_at = utcnow()
        session.add(
            TaskStateEvent(
                paper_task_id=task.id,
                from_state=previous,
                to_state=state,
                event_type="transition",
                actor="codex_results_import",
                reason_code=None,
                details={"batch_id": batch_id, "decision": decision},
            )
        )
        created += 1
    return created


def import_codex_results(
    *,
    database_url: str,
    task_package_path: Path,
    screening_results_path: Path,
    mvp_results_path: Path,
    prompt_spec_path: Path,
    edition_date: date,
    edition_type: str = "weekly",
    title: str | None = None,
) -> CodexImportSummary:
    validation = validate_mvp_results(
        task_package_path,
        screening_results_path,
        mvp_results_path,
    )
    task_package = _load_json(task_package_path)
    screening_package = _load_json(screening_results_path)
    mvp_package = _load_json(mvp_results_path)
    batch_id = validation["batch_id"]
    pipeline_run_ids = task_package["batch"]["pipeline_run_ids"]
    if len(pipeline_run_ids) != 1:
        raise CodexImportError("current publication schema requires exactly one pipeline run")
    pipeline_run_id = pipeline_run_ids[0]
    if task_package["prompt_spec"]["sha256"] != screening_package["prompt"]["prompt_spec_sha256"]:
        raise CodexImportError("task and screening prompt specification hashes differ")

    prompt_versions = {
        "paper_relevance": screening_package["prompt"]["version"],
        **mvp_package["prompts"],
    }
    tasks = {item["paper_task_id"]: item for item in task_package["tasks"]}
    screening = {
        item["paper_task_id"]: item for item in screening_package["results"]
    }
    mvp_items = {item["paper_task_id"]: item for item in mvp_package["items"]}
    screening_finished_at = _parse_timestamp(screening_package["generated_at"])
    mvp_finished_at = _parse_timestamp(mvp_package["generated_at"])

    counters = {
        "prompt_versions_created": 0,
        "ai_runs_created": 0,
        "analyses_created": 0,
        "evidence_created": 0,
        "categories_created": 0,
        "report_items_created": 0,
        "state_events_created": 0,
    }
    engine = make_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            pipeline_run = session.get(PipelineRun, pipeline_run_id)
            if pipeline_run is None:
                raise CodexImportError(f"pipeline run {pipeline_run_id} does not exist")
            prompt_rows, created = _ensure_prompt_versions(
                session,
                prompt_spec_path=prompt_spec_path,
                prompt_spec_sha256=task_package["prompt_spec"]["sha256"],
                prompt_versions=prompt_versions,
            )
            counters["prompt_versions_created"] += created

            existing_edition = session.scalar(
                select(PublicationBatch).where(
                    PublicationBatch.edition_date == edition_date,
                    PublicationBatch.edition_type == edition_type,
                )
            )
            batch = session.get(PublicationBatch, batch_id)
            expected_title = title or f"科研文献速递 {edition_date.isoformat()}"
            if batch is None:
                if existing_edition is not None:
                    raise CodexImportError(
                        f"edition {edition_date}/{edition_type} already belongs to another batch"
                    )
                batch = PublicationBatch(
                    id=batch_id,
                    pipeline_run_id=pipeline_run_id,
                    edition_date=edition_date,
                    edition_type=edition_type,
                    title=expected_title,
                    status="draft",
                )
                session.add(batch)
                session.flush()
            elif (
                batch.pipeline_run_id != pipeline_run_id
                or batch.edition_date != edition_date
                or batch.edition_type != edition_type
                or batch.title != expected_title
            ):
                raise CodexImportError(f"stored publication batch conflicts with {batch_id}")

            run_snapshot = dict(pipeline_run.config_snapshot or {})
            provenance = {
                "task_package_sha256": file_sha256(task_package_path),
                "screening_results_sha256": file_sha256(screening_results_path),
                "mvp_results_sha256": file_sha256(mvp_results_path),
                "prompt_spec_sha256": file_sha256(prompt_spec_path),
                "edition_date": edition_date.isoformat(),
                "edition_type": edition_type,
                "execution_mode": mvp_package["execution_mode"],
            }
            existing_provenance = run_snapshot.get("codex_result_import")
            if existing_provenance is not None and existing_provenance != provenance:
                raise CodexImportError("stored Codex import provenance conflicts with input files")
            run_snapshot["codex_result_import"] = provenance
            pipeline_run.config_snapshot = run_snapshot

            report_order = 0
            for task_id, screening_result in screening.items():
                task_data = tasks[task_id]
                task_row = session.get(PaperTask, task_id)
                if task_row is None:
                    raise CodexImportError(f"paper task {task_id} does not exist")
                paper_id = task_data["input_data"]["paper"]["paper_id"]
                snapshot_id = task_data["input_data"]["content_snapshot"]["snapshot_id"]
                if (
                    task_row.paper_id != paper_id
                    or task_row.pipeline_run_id != pipeline_run_id
                    or task_row.content_snapshot_id != snapshot_id
                ):
                    raise CodexImportError(f"database task identity conflicts with {task_id}")
                task_row.relevance_score = Decimal(
                    str(screening_result["output"]["relevance_score"])
                )
                _, created = _ensure_ai_run(
                    session,
                    paper_task_id=task_id,
                    prompt=prompt_rows["paper_relevance"],
                    input_sha256=screening_result["input_sha256"],
                    output=screening_result["output"],
                    execution_mode=screening_package["execution_mode"],
                    finished_at=screening_finished_at,
                )
                counters["ai_runs_created"] += created

                screening_decision = screening_result["output"]["decision"]
                if screening_decision == "irrelevant":
                    states = ["SCREENING", "IRRELEVANT"]
                elif screening_decision == "uncertain":
                    states = ["SCREENING", "REVIEW_PENDING"]
                else:
                    item = mvp_items[task_id]
                    analysis_run, created = _ensure_ai_run(
                        session,
                        paper_task_id=task_id,
                        prompt=prompt_rows["paper_analysis"],
                        input_sha256=item["input_sha256"],
                        output=item["analysis"],
                        execution_mode=mvp_package["execution_mode"],
                        finished_at=mvp_finished_at,
                    )
                    counters["ai_runs_created"] += created
                    analysis, created, evidence_created = _ensure_analysis(
                        session,
                        paper_id=paper_id,
                        ai_run=analysis_run,
                        output=item["analysis"],
                        task=task_data,
                    )
                    counters["analyses_created"] += created
                    counters["evidence_created"] += evidence_created

                    for prompt_key, output_key in (
                        ("paper_classification", "classification"),
                        ("report_composition", "report"),
                        ("report_quality_gate", "quality_gate"),
                    ):
                        _, created = _ensure_ai_run(
                            session,
                            paper_task_id=task_id,
                            prompt=prompt_rows[prompt_key],
                            input_sha256=item["input_sha256"],
                            output=item[output_key],
                            execution_mode=mvp_package["execution_mode"],
                            finished_at=mvp_finished_at,
                        )
                        counters["ai_runs_created"] += created
                    counters["categories_created"] += _ensure_categories(
                        session,
                        paper_id=paper_id,
                        classification=item["classification"],
                    )

                    quality = item["quality_gate"]
                    any_review = any(
                        item[key]["needs_human_review"]
                        for key in ("analysis", "classification", "report", "quality_gate")
                    )
                    if quality["decision"] == "reject":
                        target = "REJECTED"
                    elif quality["decision"] == "review" or any_review:
                        target = "REVIEW_PENDING"
                    else:
                        target = "READY_FOR_PREVIEW"
                        report_order += 1
                        report_item = session.scalar(
                            select(ReportItem).where(
                                ReportItem.publication_batch_id == batch_id,
                                ReportItem.paper_id == paper_id,
                            )
                        )
                        if report_item is None:
                            session.add(
                                ReportItem(
                                    publication_batch_id=batch_id,
                                    paper_id=paper_id,
                                    analysis_id=analysis.id,
                                    display_order=report_order,
                                    ai_title=item["report"]["ai_title"],
                                    summary_json=item["report"],
                                    quality_score=Decimal(str(quality["quality_score"])),
                                    review_status="auto_approved",
                                )
                            )
                            counters["report_items_created"] += 1
                        elif (
                            report_item.analysis_id != analysis.id
                            or report_item.display_order != report_order
                            or report_item.ai_title != item["report"]["ai_title"]
                            or report_item.summary_json != item["report"]
                            or report_item.quality_score
                            != Decimal(str(quality["quality_score"]))
                            or report_item.review_status != "auto_approved"
                        ):
                            raise CodexImportError(
                                f"stored report item conflicts with paper {paper_id}"
                            )
                    states = [
                        "SCREENING",
                        "ANALYZING",
                        "ANALYZED",
                        "CLASSIFYING",
                        "CLASSIFIED",
                        "COMPOSING",
                        "QUALITY_CHECK",
                        target,
                    ]
                counters["state_events_created"] += _transition_task(
                    session,
                    task=task_row,
                    states=states,
                    batch_id=batch_id,
                    decision=screening_decision,
                )

            task_count_rows = session.execute(
                select(PaperTask.state, func.count(PaperTask.id))
                .where(PaperTask.pipeline_run_id == pipeline_run_id)
                .group_by(PaperTask.state)
            ).all()
            task_counts = {state: count for state, count in task_count_rows}
            pipeline_run.status = (
                "REVIEW_PENDING" if task_counts.get("REVIEW_PENDING", 0) else "PROCESSING"
            )
            batch_status = batch.status
    finally:
        engine.dispose()

    return CodexImportSummary(
        batch_id=batch_id,
        edition_date=edition_date.isoformat(),
        batch_status=batch_status,
        task_counts=task_counts,
        **counters,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and persist manually produced Codex result packages."
    )
    parser.add_argument("--database-url", default=Settings.from_env().database_url)
    parser.add_argument("--task-package", type=Path, required=True)
    parser.add_argument("--screening-results", type=Path, required=True)
    parser.add_argument("--mvp-results", type=Path, required=True)
    parser.add_argument(
        "--prompt-spec", type=Path, default=Path("config/prompts.v1.yaml")
    )
    parser.add_argument("--edition-date", type=date.fromisoformat, required=True)
    parser.add_argument("--edition-type", default="weekly")
    parser.add_argument("--title")
    args = parser.parse_args()
    summary = import_codex_results(
        database_url=args.database_url,
        task_package_path=args.task_package,
        screening_results_path=args.screening_results,
        mvp_results_path=args.mvp_results,
        prompt_spec_path=args.prompt_spec,
        edition_date=args.edition_date,
        edition_type=args.edition_type,
        title=args.title,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
