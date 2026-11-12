from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedClient, PubMedSearchResult
from literature_intelligence.config import Settings
from literature_intelligence.config_validation import validate_v01_configuration
from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import PaperIdentifier


class PubMedSearchSource(Protocol):
    def search(
        self,
        term: str,
        *,
        retmax: int = 20,
        mindate: str | None = None,
        maxdate: str | None = None,
        datetype: str = "pdat",
        sort: str = "pub date",
    ) -> PubMedSearchResult: ...


@dataclass(frozen=True)
class QuerySpec:
    domain_code: str
    domain_name: str
    route: str
    query: str
    limit: int
    mindate: str
    maxdate: str
    datetype: str
    sort: str


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configured_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        raise


def _journal_block(journals: dict[str, Any], domain_code: str) -> str:
    selected = [*journals["global_s"], *journals["domains"][domain_code]["A"]]
    terms = []
    seen: set[str] = set()
    for journal in selected:
        pubmed_ta = journal["pubmed_ta"].strip()
        key = pubmed_ta.casefold()
        if key in seen:
            continue
        seen.add(key)
        escaped = pubmed_ta.replace('"', '\\"')
        terms.append(f'"{escaped}"[ta]')
    return "(" + " OR ".join(terms) + ")"


def build_query_specs(
    *,
    domains: dict[str, Any],
    update: dict[str, Any],
    journal_tiers: dict[str, Any],
    run_date: date,
) -> list[QuerySpec]:
    retrieval = update["retrieval"]
    lookback_days = retrieval["lookback_days"]
    window_start = run_date - timedelta(days=lookback_days - 1)
    mindate = window_start.strftime("%Y/%m/%d")
    maxdate = run_date.strftime("%Y/%m/%d")
    datetype = retrieval["pubmed_date_field"]
    sort = retrieval["sort"]
    base_filter = retrieval["base_filter"].strip()
    specs: list[QuerySpec] = []

    for domain in domains["domains"]:
        domain_block = domain["pubmed"]["concept_block"].strip()
        additional_filter = domain["pubmed"].get("additional_filter")
        for route_name, route in retrieval["routes"].items():
            if route_name == "journal_radar":
                route_block = _journal_block(journal_tiers, domain["code"])
            else:
                route_block = route["concept_block"].strip()
            parts = [f"({domain_block})", f"({route_block})", base_filter]
            if additional_filter:
                parts.append(additional_filter.strip())
            specs.append(
                QuerySpec(
                    domain_code=domain["code"],
                    domain_name=domain["name_zh"],
                    route=route_name,
                    query=" AND ".join(parts[:2]) + " AND " + " ".join(parts[2:]),
                    limit=route["limit"],
                    mindate=mindate,
                    maxdate=maxdate,
                    datetype=datetype,
                    sort=sort,
                )
            )
    return specs


def load_historical_pmids(database_url: str | None) -> set[str]:
    if not database_url:
        return set()
    if database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
        if not database_path.exists():
            raise FileNotFoundError(f"history database does not exist: {database_path}")
    engine = make_engine(database_url)
    try:
        with Session(engine) as session:
            values = session.scalars(
                select(PaperIdentifier.value).where(PaperIdentifier.scheme == "pmid")
            ).all()
        return {str(value).strip() for value in values if str(value).strip()}
    finally:
        engine.dispose()


def run_candidate_dry_run(
    *,
    source: PubMedSearchSource,
    specs: list[QuerySpec],
    per_domain_hard_cap: int,
    historical_pmids: set[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    history = historical_pmids or set()
    generated_at = generated_at or datetime.now(tz=timezone.utc)
    domain_results: dict[str, dict[str, Any]] = {}

    for spec in specs:
        result = source.search(
            spec.query,
            retmax=spec.limit,
            mindate=spec.mindate,
            maxdate=spec.maxdate,
            datetype=spec.datetype,
            sort=spec.sort,
        )
        domain = domain_results.setdefault(
            spec.domain_code,
            {
                "domain_code": spec.domain_code,
                "domain_name": spec.domain_name,
                "route_results": [],
                "pmid_routes": {},
            },
        )
        normalized_pmids = list(dict.fromkeys(str(pmid).strip() for pmid in result.pmids))
        domain["route_results"].append(
            {
                "route": spec.route,
                "query": spec.query,
                "date_parameters": {
                    "mindate": spec.mindate,
                    "maxdate": spec.maxdate,
                    "datetype": spec.datetype,
                    "sort": spec.sort,
                },
                "configured_limit": spec.limit,
                "pubmed_total_count": result.count,
                "returned_count": len(normalized_pmids),
                "pmids": normalized_pmids,
            }
        )
        for pmid in normalized_pmids:
            domain["pmid_routes"].setdefault(pmid, []).append(spec.route)

    global_membership: dict[str, list[str]] = {}
    domain_payloads: list[dict[str, Any]] = []
    total_pubmed_hits = 0
    total_returned_hits = 0
    total_historical_matches = 0
    total_capped = 0

    for domain in domain_results.values():
        route_results = domain["route_results"]
        total_pubmed_hits += sum(item["pubmed_total_count"] for item in route_results)
        total_returned_hits += sum(item["returned_count"] for item in route_results)
        pmid_routes = domain.pop("pmid_routes")
        unique_pmids = list(pmid_routes)
        historical = [pmid for pmid in unique_pmids if pmid in history]
        eligible = [pmid for pmid in unique_pmids if pmid not in history]
        selected = eligible[:per_domain_hard_cap]
        capped_count = max(0, len(eligible) - len(selected))
        total_historical_matches += len(historical)
        total_capped += capped_count
        candidates = [
            {"pmid": pmid, "matched_routes": pmid_routes[pmid]}
            for pmid in selected
        ]
        for pmid in selected:
            global_membership.setdefault(pmid, []).append(domain["domain_code"])
        domain.update(
            {
                "summary": {
                    "route_hit_count_sum": sum(item["pubmed_total_count"] for item in route_results),
                    "route_returned_count_sum": sum(item["returned_count"] for item in route_results),
                    "unique_returned_before_history_dedupe": len(unique_pmids),
                    "cross_route_overlap_count": sum(1 for routes in pmid_routes.values() if len(routes) > 1),
                    "historical_duplicate_count": len(historical),
                    "eligible_new_before_cap": len(eligible),
                    "selected_new_after_cap": len(selected),
                    "capped_count": capped_count,
                },
                "historical_duplicate_pmids": historical,
                "candidates": candidates,
            }
        )
        domain_payloads.append(domain)

    cross_domain_pmids = {
        pmid: codes for pmid, codes in global_membership.items() if len(codes) > 1
    }
    first_spec = specs[0] if specs else None
    return {
        "schema_version": "0.1",
        "run_type": "pubmed_candidate_dry_run",
        "generated_at": generated_at.isoformat(),
        "write_policy": "no_database_writes",
        "date_window": {
            "start": first_spec.mindate.replace("/", "-") if first_spec else None,
            "end": first_spec.maxdate.replace("/", "-") if first_spec else None,
            "pubmed_date_field": first_spec.datetype if first_spec else None,
        },
        "deduplication": {
            "within_domain": "PMID union across candidate routes",
            "historical_at_search_stage": "PMID exact match",
            "doi": "deferred to EFetch/normalization stage",
            "cross_domain": "retained as multi-domain membership",
            "historical_pmid_count_loaded": len(history),
        },
        "summary": {
            "query_count": len(specs),
            "domain_count": len(domain_payloads),
            "route_hit_count_sum": total_pubmed_hits,
            "route_returned_count_sum": total_returned_hits,
            "historical_duplicate_memberships": total_historical_matches,
            "capped_domain_candidates": total_capped,
            "selected_domain_memberships": sum(len(item["candidates"]) for item in domain_payloads),
            "global_unique_new_pmids": len(global_membership),
            "cross_domain_pmid_count": len(cross_domain_pmids),
        },
        "cross_domain_candidates": [
            {"pmid": pmid, "domain_codes": codes}
            for pmid, codes in cross_domain_pmids.items()
        ],
        "domains": domain_payloads,
    }


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Compose V0.1 queries and run a read-only PubMed candidate dry-run.")
    parser.add_argument("--domains", type=Path, default=Path("config/domains.v1.json"))
    parser.add_argument("--update", type=Path, default=Path("config/update.v1.json"))
    parser.add_argument("--journal-tiers", type=Path, default=Path("config/journal_tiers.v0.1.json"))
    parser.add_argument("--site", type=Path, default=Path("config/site.v2.json"))
    parser.add_argument("--run-date", type=date.fromisoformat)
    parser.add_argument("--history-database-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    validate_v01_configuration(args.domains, args.update, args.journal_tiers, args.site)
    domains = _load_json(args.domains)
    update = _load_json(args.update)
    journal_tiers = _load_json(args.journal_tiers)
    configured_timezone = _configured_timezone(update["schedule"]["timezone"])
    run_date = args.run_date or datetime.now(configured_timezone).date()
    specs = build_query_specs(
        domains=domains,
        update=update,
        journal_tiers=journal_tiers,
        run_date=run_date,
    )
    history = load_historical_pmids(args.history_database_url)
    pubmed = PubMedClient(
        api_key=settings.ncbi_api_key,
        email=settings.ncbi_email,
        tool=settings.ncbi_tool,
        timeout_seconds=20,
        max_attempts=3,
    )
    report = run_candidate_dry_run(
        source=pubmed,
        specs=specs,
        per_domain_hard_cap=update["retrieval"]["per_domain_hard_cap"],
        historical_pmids=history,
        generated_at=datetime.now(configured_timezone),
    )
    report["configuration"] = {
        "domains": {"path": args.domains.as_posix(), "sha256": _sha256(args.domains)},
        "update": {"path": args.update.as_posix(), "sha256": _sha256(args.update)},
        "journal_tiers": {"path": args.journal_tiers.as_posix(), "sha256": _sha256(args.journal_tiers)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": args.output.as_posix(), **report["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
