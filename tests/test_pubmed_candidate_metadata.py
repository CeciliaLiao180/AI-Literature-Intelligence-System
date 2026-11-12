from __future__ import annotations

import unittest

from literature_intelligence.pipeline.pubmed_candidate_metadata import rank_candidate_pmids


class CandidateMetadataTests(unittest.TestCase):
    def test_ranking_prioritizes_multi_route_candidates_and_deduplicates_domains(self) -> None:
        report = {
            "domains": [
                {
                    "domain_code": "gut_health",
                    "candidates": [
                        {"pmid": "1", "matched_routes": ["journal_radar"]},
                        {
                            "pmid": "2",
                            "matched_routes": ["frontier_radar", "clinical_radar"],
                        },
                    ],
                },
                {
                    "domain_code": "immune_health",
                    "candidates": [
                        {"pmid": "2", "matched_routes": ["journal_radar"]},
                        {"pmid": "3", "matched_routes": ["business_relevance_radar"]},
                    ],
                },
            ]
        }
        ranked = rank_candidate_pmids(report, per_domain_limit=1)
        self.assertEqual([item["pmid"] for item in ranked], ["2"])
        self.assertEqual(
            ranked[0]["candidate_domains"], ["gut_health", "immune_health"]
        )
        self.assertEqual(
            ranked[0]["domain_signals"]["gut_health"]["route_score"], 7
        )

    def test_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            rank_candidate_pmids({"domains": []}, per_domain_limit=0)


if __name__ == "__main__":
    unittest.main()
