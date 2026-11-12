from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from literature_intelligence.collectors.pubmed import PubMedSearchResult
from literature_intelligence.pipeline.pubmed_candidate_dry_run import (
    QuerySpec,
    _configured_timezone,
    build_query_specs,
    run_candidate_dry_run,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QueueSearchSource:
    def __init__(self, results: list[tuple[int, tuple[str, ...]]]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        term: str,
        *,
        retmax: int = 20,
        mindate: str | None = None,
        maxdate: str | None = None,
        datetype: str = "pdat",
        sort: str = "pub date",
    ) -> PubMedSearchResult:
        self.calls.append(
            {
                "term": term,
                "retmax": retmax,
                "mindate": mindate,
                "maxdate": maxdate,
                "datetype": datetype,
                "sort": sort,
            }
        )
        count, pmids = self.results.pop(0)
        return PubMedSearchResult(query=term, count=count, pmids=pmids)


def _spec(domain: str, route: str) -> QuerySpec:
    return QuerySpec(
        domain_code=domain,
        domain_name=domain,
        route=route,
        query=f"{domain} AND {route}",
        limit=50,
        mindate="2026/07/28",
        maxdate="2026/09/10",
        datetype="edat",
        sort="relevance",
    )


class PubMedCandidateDryRunTests(unittest.TestCase):
    def test_shanghai_timezone_has_a_dependency_free_fallback(self) -> None:
        value = datetime(2026, 9, 10, 12, tzinfo=_configured_timezone("Asia/Shanghai"))
        self.assertEqual(value.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_builds_sixteen_queries_for_four_domains_and_routes(self) -> None:
        domains = json.loads(
            (PROJECT_ROOT / "config" / "domains.v1.json").read_text(encoding="utf-8")
        )
        update = json.loads(
            (PROJECT_ROOT / "config" / "update.v1.json").read_text(encoding="utf-8")
        )
        journals = json.loads(
            (PROJECT_ROOT / "config" / "journal_tiers.v0.1.json").read_text(encoding="utf-8")
        )
        specs = build_query_specs(
            domains=domains,
            update=update,
            journal_tiers=journals,
            run_date=date(2026, 9, 10),
        )

        self.assertEqual(len(specs), 16)
        self.assertEqual({spec.domain_code for spec in specs}, {
            "gut_health", "healthy_aging", "musculoskeletal_health", "immune_health"
        })
        self.assertEqual({spec.route for spec in specs}, {
            "journal_radar", "frontier_radar", "clinical_radar", "business_relevance_radar"
        })
        self.assertTrue(all(spec.mindate == "2026/07/28" for spec in specs))
        self.assertTrue(all(spec.maxdate == "2026/09/10" for spec in specs))
        self.assertTrue(all(spec.datetype == "edat" for spec in specs))
        gut_journal = next(
            spec for spec in specs
            if spec.domain_code == "gut_health" and spec.route == "journal_radar"
        )
        self.assertIn('"Nature"[ta]', gut_journal.query)
        self.assertIn('"Gut"[ta]', gut_journal.query)
        self.assertIn("hasabstract NOT", gut_journal.query)
        musculoskeletal = next(
            spec for spec in specs
            if spec.domain_code == "musculoskeletal_health" and spec.route == "clinical_radar"
        )
        self.assertIn("arthroplasty[ti]", musculoskeletal.query)

    def test_deduplicates_routes_and_history_but_retains_cross_domain_membership(self) -> None:
        specs = [
            _spec("gut_health", "journal_radar"),
            _spec("gut_health", "frontier_radar"),
            _spec("immune_health", "journal_radar"),
        ]
        source = QueueSearchSource(
            [
                (100, ("1", "2", "3")),
                (80, ("2", "4")),
                (20, ("2", "5")),
            ]
        )
        report = run_candidate_dry_run(
            source=source,
            specs=specs,
            per_domain_hard_cap=2,
            historical_pmids={"3", "99"},
            generated_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )

        gut = report["domains"][0]
        self.assertEqual(gut["summary"]["unique_returned_before_history_dedupe"], 4)
        self.assertEqual(gut["summary"]["cross_route_overlap_count"], 1)
        self.assertEqual(gut["summary"]["historical_duplicate_count"], 1)
        self.assertEqual(gut["summary"]["capped_count"], 1)
        self.assertEqual([item["pmid"] for item in gut["candidates"]], ["1", "2"])
        self.assertEqual(gut["historical_duplicate_pmids"], ["3"])

        self.assertEqual(report["summary"]["global_unique_new_pmids"], 3)
        self.assertEqual(report["summary"]["cross_domain_pmid_count"], 1)
        self.assertEqual(
            report["cross_domain_candidates"],
            [{"pmid": "2", "domain_codes": ["gut_health", "immune_health"]}],
        )
        self.assertEqual(source.calls[0]["datetype"], "edat")
        self.assertEqual(source.calls[0]["mindate"], "2026/07/28")


if __name__ == "__main__":
    unittest.main()
