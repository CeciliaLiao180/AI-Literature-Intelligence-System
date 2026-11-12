from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from literature_intelligence.config_validation import (
    ConfigValidationError,
    validate_v01_configuration,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class V01ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = {
            "domain_path": PROJECT_ROOT / "config" / "domains.v1.json",
            "update_path": PROJECT_ROOT / "config" / "update.v1.json",
            "journal_tiers_path": PROJECT_ROOT / "config" / "journal_tiers.v0.1.json",
            "site_path": PROJECT_ROOT / "config" / "site.v2.json",
        }

    def _load(self, key: str) -> dict:
        return json.loads(self.paths[key].read_text(encoding="utf-8"))

    def _validate_with_override(self, key: str, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            override = Path(temp_dir) / "override.json"
            override.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            paths = dict(self.paths)
            paths[key] = override
            validate_v01_configuration(**paths)

    def test_repository_v01_configuration_is_valid(self) -> None:
        result = validate_v01_configuration(**self.paths)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["lookback_days"], 45)
        self.assertEqual(result["score_total"], 100)
        self.assertEqual(len(result["domain_codes"]), 4)
        self.assertEqual(result["journal_counts"], {"global_s": 12, "domain_a": 37, "domain_b": 46})
        self.assertFalse(result["schedule_enabled"])
        self.assertFalse(result["automatic_publish_enabled"])

    def test_rejects_score_weights_that_do_not_sum_to_100(self) -> None:
        payload = deepcopy(self._load("update_path"))
        payload["scoring"]["weights"]["recency"] = 9
        with self.assertRaisesRegex(ConfigValidationError, "sum to 100"):
            self._validate_with_override("update_path", payload)

    def test_rejects_duplicate_journal_within_domain_tiers(self) -> None:
        payload = deepcopy(self._load("journal_tiers_path"))
        payload["domains"]["gut_health"]["B"].append(
            deepcopy(payload["domains"]["gut_health"]["A"][0])
        )
        with self.assertRaisesRegex(ConfigValidationError, "same journal in A and B"):
            self._validate_with_override("journal_tiers_path", payload)

    def test_rejects_site_domain_code_drift(self) -> None:
        payload = deepcopy(self._load("site_path"))
        payload["domains"][1]["code"] = "immune_modulation"
        with self.assertRaisesRegex(ConfigValidationError, "site domain codes"):
            self._validate_with_override("site_path", payload)

    def test_rejects_automatic_publication(self) -> None:
        payload = deepcopy(self._load("update_path"))
        payload["execution"]["automatic_publish_enabled"] = True
        with self.assertRaisesRegex(ConfigValidationError, "automatic publication"):
            self._validate_with_override("update_path", payload)


if __name__ == "__main__":
    unittest.main()
