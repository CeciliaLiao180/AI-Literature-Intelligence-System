from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from literature_intelligence.collectors.pubmed import PubMedClient
from literature_intelligence.config import Settings


ROUTE_POINTS = {
    "journal_radar": 4,
    "frontier_radar": 3,
    "clinical_radar": 2,
    "business_relevance_radar": 1,
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def rank_candidate_pmids(
    report: dict[str, Any], *, per_domain_limit: int
) -> list[dict[str, Any]]:
    if per_domain_limit < 1:
        raise ValueError("per_domain_limit must be at least 1")
    selected: dict[str, dict[str, Any]] = {}
    for domain in report["domains"]:
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for position, candidate in enumerate(domain["candidates"]):
            routes = candidate["matched_routes"]
            route_score = sum(ROUTE_POINTS.get(route, 0) for route in routes)
            overlap_bonus = max(0, len(routes) - 1) * 2
            ranked.append((route_score + overlap_bonus, -position, candidate))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for rank, (score, _, candidate) in enumerate(ranked[:per_domain_limit], start=1):
            pmid = candidate["pmid"]
            row = selected.setdefault(
                pmid,
                {
                    "pmid": pmid,
                    "candidate_domains": [],
                    "domain_signals": {},
                },
            )
            row["candidate_domains"].append(domain["domain_code"])
            row["domain_signals"][domain["domain_code"]] = {
                "rank": rank,
                "route_score": score,
                "matched_routes": candidate["matched_routes"],
            }
    return list(selected.values())


def build_metadata_package(
    *,
    report: dict[str, Any],
    client: PubMedClient,
    per_domain_limit: int,
) -> dict[str, Any]:
    ranked = rank_candidate_pmids(report, per_domain_limit=per_domain_limit)
    pmids = tuple(item["pmid"] for item in ranked)
    if len(pmids) > 200:
        raise ValueError("metadata shortlist exceeds the PubMed EFetch limit of 200")
    records = {record.pmid: record for record in client.fetch(pmids)}
    items: list[dict[str, Any]] = []
    for candidate in ranked:
        record = records.get(candidate["pmid"])
        if record is None:
            continue
        items.append(
            {
                **candidate,
                "record": record.to_source_payload(),
                "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{record.pmid}/",
                "doi_url": f"https://doi.org/{record.doi}" if record.doi else None,
            }
        )
    return {
        "schema_version": "0.1",
        "package_type": "pubmed_candidate_metadata_shortlist",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dry_run": {
            "run_type": report.get("run_type"),
            "date_window": report.get("date_window"),
        },
        "selection_policy": {
            "per_domain_limit": per_domain_limit,
            "route_points": ROUTE_POINTS,
            "cross_route_overlap_bonus_per_extra_route": 2,
            "database_writes": False,
        },
        "summary": {
            "requested_unique_pmids": len(pmids),
            "fetched_records": len(items),
            "missing_records": len(pmids) - len(items),
        },
        "missing_pmids": [pmid for pmid in pmids if pmid not in records],
        "items": items,
    }


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Fetch PubMed metadata for a deterministic dry-run shortlist.")
    parser.add_argument("--dry-run", type=Path, required=True)
    parser.add_argument("--per-domain-limit", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = _load_json(args.dry_run)
    client = PubMedClient(
        api_key=settings.ncbi_api_key,
        email=settings.ncbi_email,
        tool=settings.ncbi_tool,
        timeout_seconds=30,
        max_attempts=3,
    )
    package = build_metadata_package(
        report=report,
        client=client,
        per_domain_limit=args.per_domain_limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": args.output.as_posix(), **package["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
