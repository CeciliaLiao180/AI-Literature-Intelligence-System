from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from literature_intelligence.collectors.pubmed import (
    PubMedClient,
    PubMedRecord,
    PubMedSearchResult,
)
from literature_intelligence.config import Settings
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    Paper,
    PaperTask,
    PipelineRun,
    ResearchDomain,
    SearchQuery,
    SourceRecord,
    TaskStateEvent,
    utcnow,
)
from literature_intelligence.services.normalization import normalize_pmid
from literature_intelligence.services.paper_ingest import ingest_pubmed_record
from literature_intelligence.services.verification import (
    VerificationResult,
    VerificationService,
    persist_verification_results,
)


class PubMedSource(Protocol):
    def search(self, term: str, *, retmax: int = 20) -> PubMedSearchResult: ...

    def fetch(self, pmids: tuple[str, ...]) -> list[PubMedRecord]: ...


class Verifier(Protocol):
    def verify_pmid(self, value: str) -> VerificationResult: ...

    def verify_doi(self, value: str) -> VerificationResult: ...

    def verify_url(self, value: str) -> VerificationResult: ...


@dataclass(frozen=True)
class IngestionConfig:
    query: str
    domain_code: str
    domain_name_zh: str
    domain_description: str | None = None
    retmax: int = 10
    trigger_type: str = "manual"


@dataclass(frozen=True)
class IngestionSummary:
    pipeline_run_id: str
    source_total_count: int
    requested_count: int
    fetched_count: int
    task_counts: dict[str, int]
    status: str


class IngestionPipeline:
    def __init__(
        self,
        *,
        engine: Engine,
        pubmed: PubMedSource,
        verifier: Verifier,
        actor: str = "ingestion_pipeline",
    ) -> None:
        self.engine = engine
        self.pubmed = pubmed
        self.verifier = verifier
        self.actor = actor
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def run(self, config: IngestionConfig) -> IngestionSummary:
        run_id, search_query_id = self._create_run(config)
        try:
            self._update_run(run_id, status="COLLECTING", started=True)
            search_result = self.pubmed.search(config.query, retmax=config.retmax)
            records = self.pubmed.fetch(search_result.pmids)
            self._record_search_counts(run_id, search_result, len(records))

            for record in records:
                task_id, should_verify = self._persist_candidate(
                    run_id, search_query_id, record
                )
                if should_verify:
                    results = self._verify_record(record)
                    self._persist_verification(task_id, results)

            task_counts = self._task_counts(run_id)
            final_status = self._derive_batch_status(search_result.pmids, task_counts)
            self._update_run(run_id, status=final_status, finished=final_status == "COMPLETED")
            return IngestionSummary(
                pipeline_run_id=run_id,
                source_total_count=search_result.count,
                requested_count=len(search_result.pmids),
                fetched_count=len(records),
                task_counts=task_counts,
                status=final_status,
            )
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise

    def _create_run(self, config: IngestionConfig) -> tuple[str, int]:
        with self.sessions.begin() as session:
            domain = session.scalar(
                select(ResearchDomain).where(ResearchDomain.code == config.domain_code)
            )
            if domain is None:
                domain = ResearchDomain(
                    code=config.domain_code,
                    name_zh=config.domain_name_zh,
                    description=config.domain_description,
                    is_active=True,
                )
                session.add(domain)
                session.flush()
            else:
                domain.name_zh = config.domain_name_zh
                domain.is_active = True
                if config.domain_description is not None:
                    domain.description = config.domain_description

            query_row = session.scalar(
                select(SearchQuery).where(
                    SearchQuery.domain_id == domain.id,
                    SearchQuery.source == "pubmed",
                    SearchQuery.query_text == config.query,
                    SearchQuery.is_active.is_(True),
                )
            )
            if query_row is None:
                latest_version = session.scalar(
                    select(func.max(SearchQuery.version)).where(
                        SearchQuery.domain_id == domain.id,
                        SearchQuery.source == "pubmed",
                    )
                )
                query_row = SearchQuery(
                    domain_id=domain.id,
                    source="pubmed",
                    query_text=config.query,
                    lookback_days=7,
                    version=(latest_version or 0) + 1,
                    is_active=True,
                )
                session.add(query_row)
                session.flush()

            run = PipelineRun(
                trigger_type=config.trigger_type,
                status="CREATED",
                config_snapshot={
                    "stage": "verified_ingestion",
                    "query": config.query,
                    "domain_code": config.domain_code,
                    "domain_description": config.domain_description,
                    "retmax": config.retmax,
                    "require_batch_publish_confirmation": True,
                },
            )
            session.add(run)
            session.flush()
            return run.id, query_row.id

    def _persist_candidate(
        self, run_id: str, search_query_id: int, record: PubMedRecord
    ) -> tuple[str, bool]:
        payload = record.to_source_payload()
        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        pmid = normalize_pmid(record.pmid)
        if pmid is None:
            raise ValueError("PubMed returned an invalid PMID")

        with self.sessions.begin() as session:
            source = SourceRecord(
                pipeline_run_id=run_id,
                search_query_id=search_query_id,
                source="pubmed",
                external_id=pmid,
                raw_payload=payload,
                payload_sha256=digest,
            )
            session.add(source)
            session.flush()
            ingest = ingest_pubmed_record(session, record, source_record=source)

            task = session.scalar(
                select(PaperTask).where(
                    PaperTask.pipeline_run_id == run_id,
                    PaperTask.paper_id == ingest.paper_id,
                )
            )
            if task is not None:
                return task.id, False

            task = PaperTask(
                pipeline_run_id=run_id,
                paper_id=ingest.paper_id,
                content_snapshot_id=ingest.content_snapshot_id,
                state="DISCOVERED",
            )
            session.add(task)
            session.flush()
            self._transition(session, task, "DISCOVERED", from_state=None)
            self._transition(session, task, "NORMALIZED")
            if ingest.needs_human_review:
                self._transition(
                    session,
                    task,
                    "REVIEW_PENDING",
                    reason_code="POSSIBLE_TITLE_YEAR_DUPLICATE",
                    details={"possible_duplicate_id": ingest.possible_duplicate_id},
                )
                return task.id, False
            self._transition(session, task, "VERIFYING")
            return task.id, True

    def _verify_record(self, record: PubMedRecord) -> list[VerificationResult]:
        pmid = normalize_pmid(record.pmid)
        if pmid is None:
            raise ValueError("PubMed returned an invalid PMID")
        results = [self.verifier.verify_pmid(pmid)]
        if record.doi:
            results.append(self.verifier.verify_doi(record.doi))
        results.append(
            self.verifier.verify_url(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        )
        return results

    def _persist_verification(
        self, task_id: str, results: list[VerificationResult]
    ) -> None:
        with self.sessions.begin() as session:
            task = session.get(PaperTask, task_id)
            if task is None:
                raise RuntimeError(f"paper task {task_id} no longer exists")
            if task.state != "VERIFYING":
                return
            persist_verification_results(session, task_id, results)
            statuses = {result.status for result in results}
            paper = session.get(Paper, task.paper_id)

            if "temporary_error" in statuses:
                task.resume_state = "VERIFYING"
                task.next_retry_at = utcnow() + timedelta(seconds=30)
                task.last_error_code = "VERIFICATION_TEMPORARY_ERROR"
                self._transition(
                    session,
                    task,
                    "RETRY_WAIT",
                    reason_code=task.last_error_code,
                )
                if paper:
                    paper.metadata_status = "unverified"
            elif "conflict" in statuses:
                self._transition(
                    session,
                    task,
                    "REVIEW_PENDING",
                    reason_code="VERIFICATION_CONFLICT",
                )
                if paper:
                    paper.metadata_status = "conflict"
            elif "invalid" in statuses:
                self._transition(
                    session,
                    task,
                    "REJECTED",
                    reason_code="VERIFICATION_INVALID",
                )
                if paper:
                    paper.metadata_status = "unverified"
            else:
                self._transition(session, task, "VERIFIED")
                if paper:
                    paper.metadata_status = "verified"

    def _transition(
        self,
        session: Session,
        task: PaperTask,
        to_state: str,
        *,
        from_state: str | None = "CURRENT",
        reason_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        actual_from = task.state if from_state == "CURRENT" else from_state
        task.state = to_state
        task.state_changed_at = utcnow()
        session.add(
            TaskStateEvent(
                paper_task_id=task.id,
                from_state=actual_from,
                to_state=to_state,
                event_type="transition",
                actor=self.actor,
                reason_code=reason_code,
                details=details or {},
            )
        )

    def _record_search_counts(
        self, run_id: str, result: PubMedSearchResult, fetched_count: int
    ) -> None:
        with self.sessions.begin() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(f"pipeline run {run_id} no longer exists")
            run.total_candidates = len(result.pmids)
            missing = len(result.pmids) - fetched_count
            if missing > 0:
                run.failed_count = missing
                run.error_summary = f"{missing} requested PMID records were not returned by EFetch"

    def _task_counts(self, run_id: str) -> dict[str, int]:
        with self.sessions() as session:
            rows = session.execute(
                select(PaperTask.state, func.count(PaperTask.id))
                .where(PaperTask.pipeline_run_id == run_id)
                .group_by(PaperTask.state)
            ).all()
            return {state: count for state, count in rows}

    @staticmethod
    def _derive_batch_status(
        requested_pmids: tuple[str, ...], task_counts: dict[str, int]
    ) -> str:
        if not requested_pmids:
            return "COMPLETED"
        if task_counts.get("REVIEW_PENDING", 0):
            return "REVIEW_PENDING"
        return "PROCESSING"

    def _update_run(
        self,
        run_id: str,
        *,
        status: str,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        with self.sessions.begin() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(f"pipeline run {run_id} no longer exists")
            run.status = status
            if started:
                run.started_at = utcnow()
            if finished:
                run.finished_at = utcnow()

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        with self.sessions.begin() as session:
            run = session.get(PipelineRun, run_id)
            if run is not None:
                run.status = "FAILED"
                run.finished_at = utcnow()
                run.error_summary = f"{type(exc).__name__}: {str(exc)[:500]}"


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Run PubMed ingestion through verification.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--domain-code", required=True)
    parser.add_argument("--domain-name", required=True)
    parser.add_argument("--domain-description")
    parser.add_argument("--retmax", type=int, default=1)
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args()

    engine = create_database(args.database_url)
    pubmed = PubMedClient(
        api_key=settings.ncbi_api_key,
        email=settings.ncbi_email,
        tool=settings.ncbi_tool,
        timeout_seconds=15,
        max_attempts=2,
    )
    verifier = VerificationService(
        pubmed_client=pubmed,
        crossref_email=settings.crossref_email,
        timeout_seconds=10,
    )
    summary = IngestionPipeline(
        engine=engine,
        pubmed=pubmed,
        verifier=verifier,
    ).run(
        IngestionConfig(
            query=args.query,
            domain_code=args.domain_code,
            domain_name_zh=args.domain_name,
            domain_description=args.domain_description,
            retmax=args.retmax,
        )
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True))
    engine.dispose()


if __name__ == "__main__":
    main()
