from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_DOMAIN_CODES = {
    "gut_health",
    "healthy_aging",
    "musculoskeletal_health",
    "immune_health",
}
EXPECTED_ROUTE_NAMES = {
    "journal_radar",
    "frontier_radar",
    "clinical_radar",
    "business_relevance_radar",
}
EXPECTED_SCORE_KEYS = {
    "domain_relevance",
    "novelty_frontier_value",
    "journal_source_quality",
    "evidence_strength",
    "nutrition_natural_product_relevance",
    "recency",
}
REQUIRED_FRONTIER_TYPES = {
    "NEW_MECHANISM",
    "NEW_TARGET",
    "NEW_BIOMARKER",
    "NEW_TECHNOLOGY",
    "NEW_METHOD",
    "NEW_MODEL",
    "NEW_INTERVENTION",
    "NEW_CLINICAL_EVIDENCE",
    "FIRST_IN_HUMAN",
}


class ConfigValidationError(ValueError):
    """Raised when the V0.1 configuration set is internally inconsistent."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(f"cannot load {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigValidationError(f"{path} must contain a JSON object")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigValidationError(message)


def _non_blank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_domain_catalog(payload: dict[str, Any]) -> set[str]:
    domains = payload.get("domains")
    _require(isinstance(domains, list), "domains must be a list")
    codes = [item.get("code") for item in domains if isinstance(item, dict)]
    _require(len(codes) == len(domains), "every domain must be an object")
    _require(len(codes) == len(set(codes)), "domain codes must be unique")
    _require(set(codes) == EXPECTED_DOMAIN_CODES, "domain catalog must contain exactly the four V0.1 domains")

    for item in domains:
        code = item["code"]
        _require(item.get("is_active") is True, f"{code} must be active in V0.1")
        _require(_non_blank(item.get("name_zh")), f"{code}.name_zh must not be blank")
        _require(_non_blank(item.get("description")), f"{code}.description must not be blank")
        topics = item.get("priority_topics")
        _require(isinstance(topics, list) and topics, f"{code}.priority_topics must not be empty")
        pubmed = item.get("pubmed")
        _require(isinstance(pubmed, dict), f"{code}.pubmed must be an object")
        _require(_non_blank(pubmed.get("concept_block")), f"{code}.pubmed.concept_block must not be blank")
        additional_filter = pubmed.get("additional_filter")
        _require(
            additional_filter is None or _non_blank(additional_filter),
            f"{code}.pubmed.additional_filter must be null or non-blank",
        )

    multidomain = payload.get("multidomain_policy")
    _require(isinstance(multidomain, dict) and multidomain.get("enabled") is True, "multi-domain classification must be enabled")
    _require(multidomain.get("primary_field") == "primary_domain", "primary domain field must be primary_domain")
    _require(multidomain.get("secondary_field") == "secondary_domains", "secondary domain field must be secondary_domains")
    return set(codes)


def _validate_update_config(payload: dict[str, Any]) -> None:
    retrieval = payload.get("retrieval")
    _require(isinstance(retrieval, dict), "retrieval must be an object")
    _require(retrieval.get("lookback_days") == 45, "PubMed lookback must be 45 days")
    _require(str(retrieval.get("pubmed_date_field", "")).lower() == "edat", "PubMed date field must be EDAT")
    _require(retrieval.get("per_domain_hard_cap") == 200, "per-domain hard cap must be 200")
    dedupe = retrieval.get("deduplication_keys")
    _require(isinstance(dedupe, list) and {"pmid", "doi", "historical_processing_state"}.issubset(dedupe), "deduplication must cover PMID, DOI and historical state")

    routes = retrieval.get("routes")
    _require(isinstance(routes, dict) and set(routes) == EXPECTED_ROUTE_NAMES, "candidate routes do not match the V0.1 contract")
    for route_name, route in routes.items():
        _require(isinstance(route, dict), f"{route_name} must be an object")
        _require(isinstance(route.get("limit"), int) and route["limit"] > 0, f"{route_name}.limit must be positive")
        _require(_non_blank(route.get("description")), f"{route_name}.description must not be blank")
        _require(
            _non_blank(route.get("concept_block")) or _non_blank(route.get("block_source")),
            f"{route_name} must define a concept block or block source",
        )

    weights = payload.get("scoring", {}).get("weights")
    _require(isinstance(weights, dict) and set(weights) == EXPECTED_SCORE_KEYS, "scoring weights do not match the V0.1 dimensions")
    _require(all(isinstance(value, int) and value >= 0 for value in weights.values()), "all scoring weights must be non-negative integers")
    _require(sum(weights.values()) == 100, "scoring weights must sum to 100")
    _require(payload["scoring"].get("journal_tier_is_hard_filter") is False, "journal tier must not be a hard filter")
    _require(payload["scoring"].get("impact_factor_is_hard_filter") is False, "impact factor must not be a hard filter")

    recency = payload.get("recency_scoring")
    _require(isinstance(recency, list) and len(recency) == 3, "recency scoring must contain three bands")
    expected_bands = [(0, 14, 10), (15, 30, 7), (31, 45, 4)]
    actual_bands = [(band.get("min_age_days"), band.get("max_age_days"), band.get("points")) for band in recency]
    _require(actual_bands == expected_bands, "recency bands must be 0-14/15-30/31-45 days")

    frontier_types = payload.get("frontier_types")
    _require(isinstance(frontier_types, list), "frontier_types must be a list")
    _require(set(frontier_types) == REQUIRED_FRONTIER_TYPES and len(frontier_types) == len(set(frontier_types)), "frontier_types must contain the required unique V0.1 values")

    schedule = payload.get("schedule")
    execution = payload.get("execution")
    _require(isinstance(schedule, dict) and schedule.get("enabled") is False, "scheduled execution must remain disabled")
    _require(isinstance(execution, dict) and execution.get("manual_run_enabled") is True, "manual execution must remain enabled")
    _require(execution.get("automatic_analysis_enabled") is False, "automatic analysis must remain disabled")
    _require(execution.get("automatic_publish_enabled") is False, "automatic publication must remain disabled")
    _require(execution.get("human_confirmation_required_before_publish") is True, "human confirmation must be required before publication")


def _journal_key(item: dict[str, Any]) -> str:
    return str(item.get("canonical_name", "")).strip().casefold()


def _validate_journal_list(items: object, label: str) -> set[str]:
    _require(isinstance(items, list) and items, f"{label} must be a non-empty list")
    keys: list[str] = []
    for index, item in enumerate(items):
        _require(isinstance(item, dict), f"{label}[{index}] must be an object")
        _require(_non_blank(item.get("canonical_name")), f"{label}[{index}].canonical_name must not be blank")
        _require(_non_blank(item.get("pubmed_ta")), f"{label}[{index}].pubmed_ta must not be blank")
        _require(isinstance(item.get("aliases"), list), f"{label}[{index}].aliases must be a list")
        keys.append(_journal_key(item))
    _require(len(keys) == len(set(keys)), f"{label} contains duplicate journals")
    return set(keys)


def _validate_journal_tiers(payload: dict[str, Any], domain_codes: set[str]) -> dict[str, int]:
    _require(payload.get("status") == "starter_pending_domain_review", "journal tiers must be marked as a starter list pending review")
    matching = payload.get("matching")
    _require(isinstance(matching, dict) and matching.get("hard_filter") is False, "journal tiers must not be a hard filter")
    _require(matching.get("impact_factor_is_authoritative") is False, "impact factor must not be authoritative")

    global_s = _validate_journal_list(payload.get("global_s"), "global_s")
    domains = payload.get("domains")
    _require(isinstance(domains, dict) and set(domains) == domain_codes, "journal tier domain keys must match the four domain codes")
    counts = {"global_s": len(global_s), "domain_a": 0, "domain_b": 0}
    for code, tiers in domains.items():
        _require(isinstance(tiers, dict) and set(tiers) == {"A", "B"}, f"{code} must define A and B tiers")
        tier_a = _validate_journal_list(tiers["A"], f"{code}.A")
        tier_b = _validate_journal_list(tiers["B"], f"{code}.B")
        _require(tier_a.isdisjoint(tier_b), f"{code} contains the same journal in A and B tiers")
        _require(global_s.isdisjoint(tier_a | tier_b), f"{code} repeats a global S-tier journal")
        counts["domain_a"] += len(tier_a)
        counts["domain_b"] += len(tier_b)
    return counts


def _validate_site_config(payload: dict[str, Any], domain_codes: set[str]) -> None:
    domains = payload.get("domains")
    _require(isinstance(domains, list), "site domains must be a list")
    codes = [item.get("code") for item in domains if isinstance(item, dict)]
    slugs = [item.get("slug") for item in domains if isinstance(item, dict)]
    _require(set(codes) == domain_codes and len(codes) == len(set(codes)), "site domain codes must match the V0.1 catalog")
    _require(all(_non_blank(slug) for slug in slugs) and len(slugs) == len(set(slugs)), "site domain slugs must be non-blank and unique")
    immune = next(item for item in domains if item.get("code") == "immune_health")
    _require(immune.get("slug") == "immune", "immune_health must keep the stable immune URL")
    _require("immune-modulation" in immune.get("aliases", []), "the previous immune-modulation URL must remain an alias")


def validate_v01_configuration(
    domain_path: Path,
    update_path: Path,
    journal_tiers_path: Path,
    site_path: Path,
) -> dict[str, Any]:
    domain_codes = _validate_domain_catalog(_load_json(domain_path))
    _validate_update_config(_load_json(update_path))
    journal_counts = _validate_journal_tiers(_load_json(journal_tiers_path), domain_codes)
    _validate_site_config(_load_json(site_path), domain_codes)
    return {
        "status": "valid",
        "domain_codes": sorted(domain_codes),
        "lookback_days": 45,
        "candidate_routes": sorted(EXPECTED_ROUTE_NAMES),
        "score_total": 100,
        "journal_counts": journal_counts,
        "schedule_enabled": False,
        "automatic_publish_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the V0.1 domain and retrieval configuration set.")
    parser.add_argument("--domains", type=Path, default=Path("config/domains.v1.json"))
    parser.add_argument("--update", type=Path, default=Path("config/update.v1.json"))
    parser.add_argument("--journal-tiers", type=Path, default=Path("config/journal_tiers.v0.1.json"))
    parser.add_argument("--site", type=Path, default=Path("config/site.v2.json"))
    args = parser.parse_args()
    result = validate_v01_configuration(args.domains, args.update, args.journal_tiers, args.site)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
