from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedClient
from literature_intelligence.config import Settings
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import PipelineRun, ResearchDomain, SearchQuery, utcnow
from literature_intelligence.pipeline.ingestion import IngestionPipeline
from literature_intelligence.services.verification import VerificationService


@dataclass(frozen=True)
class SelectedIngestionSummary:
    pipeline_run_id: str
    batch_id: str
    requested_count: int
    fetched_count: int
    task_counts: dict[str, int]
    status: str


def _load_selection(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "0.1":
        raise ValueError("unsupported selection package version")
    if not isinstance(payload.get("batch_id"), str) or not payload["batch_id"].strip():
        raise ValueError("selection package requires batch_id")
    selected = payload.get("selected")
    if not isinstance(selected, list) or not selected:
        raise ValueError("selection package requires selected candidates")
    pmids: list[str] = []
    for index, item in enumerate(selected):
        if not isinstance(item, dict):
            raise ValueError(f"selected[{index}] must be an object")
        pmid = item.get("pmid")
        domain = item.get("primary_domain")
        reason = item.get("selection_reason")
        if not isinstance(pmid, str) or not pmid.isdigit():
            raise ValueError(f"selected[{index}] has invalid PMID")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(f"selected[{index}] requires primary_domain")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"selected[{index}] requires selection_reason")
        pmids.append(pmid)
    if len(pmids) != len(set(pmids)):
        raise ValueError("selection package contains duplicate PMIDs")
    return payload


def _selection_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SelectedCandidateIngestion:
    def __init__(
        self,
        *,
        engine,
        pubmed: PubMedClient,
        verifier: VerificationService,
    ) -> None:
        self.engine = engine
        self.pubmed = pubmed
        self.pipeline = IngestionPipeline(
            engine=engine,
            pubmed=pubmed,
            verifier=verifier,
            actor="selected_candidate_ingestion",
        )

    def run(self, selection_path: Path) -> SelectedIngestionSummary:
        package = _load_selection(selection_path)
        selected = package["selected"]
        run_id, query_ids = self._create_run(package, selection_path)
        pmids = tuple(item["pmid"] for item in selected)
        try:
            records = {record.pmid: record for record in self.pubmed.fetch(pmids)}
            for item in selected:
                record = records.get(item["pmid"])
                if record is None:
                    continue
                task_id, should_verify = self.pipeline._persist_candidate(
                    run_id,
                    query_ids[item["primary_domain"]],
                    record,
                )
                if should_verify:
                    self.pipeline._persist_verification(
                        task_id,
                        self.pipeline._verify_record(record),
                    )
            task_counts = self.pipeline._task_counts(run_id)
            missing = len(pmids) - len(records)
            status = "REVIEW_PENDING" if task_counts.get("REVIEW_PENDING", 0) else "PROCESSING"
            with Session(self.engine) as session, session.begin():
                run = session.get(PipelineRun, run_id)
                if run is None:
                    raise RuntimeError(f"pipeline run {run_id} disappeared")
                run.status = status
                run.total_candidates = len(pmids)
                run.failed_count = missing
                run.error_summary = (
                    f"{missing} selected PMID records were not returned by EFetch"
                    if missing
                    else None
                )
            return SelectedIngestionSummary(
                pipeline_run_id=run_id,
                batch_id=package["batch_id"],
                requested_count=len(pmids),
                fetched_count=len(records),
                task_counts=task_counts,
                status=status,
            )
        except Exception as exc:
            self.pipeline._mark_failed(run_id, exc)
            raise

    def _create_run(
        self,
        package: dict[str, Any],
        selection_path: Path,
    ) -> tuple[str, dict[str, int]]:
        selected = package["selected"]
        domain_pmids: dict[str, list[str]] = {}
        for item in selected:
            domain_pmids.setdefault(item["primary_domain"], []).append(item["pmid"])
        with Session(self.engine) as session, session.begin():
            domains = {
                domain.code: domain
                for domain in session.scalars(
                    select(ResearchDomain).where(
                        ResearchDomain.code.in_(domain_pmids),
                        ResearchDomain.is_active.is_(True),
                    )
                )
            }
            missing = set(domain_pmids) - set(domains)
            if missing:
                raise ValueError(f"database lacks active domains: {sorted(missing)}")
            query_ids: dict[str, int] = {}
            for code, pmids in domain_pmids.items():
                latest_version = session.scalar(
                    select(func.max(SearchQuery.version)).where(
                        SearchQuery.domain_id == domains[code].id,
                        SearchQuery.source == "pubmed",
                    )
                )
                query = SearchQuery(
                    domain_id=domains[code].id,
                    source="pubmed",
                    query_text="(" + " OR ".join(f'\"{pmid}\"[PMID]' for pmid in pmids) + ")",
                    lookback_days=45,
                    version=(latest_version or 0) + 1,
                    is_active=True,
                )
                session.add(query)
                session.flush()
                query_ids[code] = query.id
            run = PipelineRun(
                trigger_type="manual",
                status="COLLECTING",
                started_at=utcnow(),
                total_candidates=len(selected),
                config_snapshot={
                    "stage": "selected_candidate_ingestion",
                    "batch_id": package["batch_id"],
                    "edition_date": package.get("edition_date"),
                    "selection_package": selection_path.as_posix(),
                    "selection_package_sha256": _selection_sha256(selection_path),
                    "selected": selected,
                    "lookback_days": 45,
                    "require_batch_publish_confirmation": True,
                },
            )
            session.add(run)
            session.flush()
            return run.id, query_ids


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Ingest a reviewed multi-domain PMID selection into one pipeline run.")
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args()
    engine = create_database(args.database_url)
    pubmed = PubMedClient(
        api_key=settings.ncbi_api_key,
        email=settings.ncbi_email,
        tool=settings.ncbi_tool,
        timeout_seconds=25,
        max_attempts=3,
    )
    verifier = VerificationService(
        pubmed_client=pubmed,
        crossref_email=settings.crossref_email,
        timeout_seconds=20,
    )
    try:
        summary = SelectedCandidateIngestion(
            engine=engine,
            pubmed=pubmed,
            verifier=verifier,
        ).run(args.selection)
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
