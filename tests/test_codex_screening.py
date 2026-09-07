from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from literature_intelligence.pipeline.codex_screening import (
    ScreeningValidationError,
    file_sha256,
    validate_screening_results,
)


class CodexScreeningValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.tasks_path = root / "tasks.json"
        self.results_path = root / "results.json"
        self.task_package = {
            "batch": {"batch_id": "batch-1"},
            "prompt_spec": {"sha256": "a" * 64},
            "tasks": [
                {
                    "paper_task_id": "task-1",
                    "input_sha256": "b" * 64,
                    "input_data": {
                        "paper": {"paper_id": 1, "identifiers": {"pmid": "123"}},
                        "domain_context": {
                            "active_domains": [{"code": "gut_health"}]
                        },
                    },
                }
            ],
        }
        self.tasks_path.write_text(
            json.dumps(self.task_package),
            encoding="utf-8",
        )
        self.results_package = {
            "result_package_version": "1.0",
            "source_task_package": {
                "path": self.tasks_path.as_posix(),
                "sha256": file_sha256(self.tasks_path),
                "batch_id": "batch-1",
            },
            "prompt": {
                "key": "paper_relevance",
                "version": "1.0.0",
                "output_schema_version": "1.0",
                "prompt_spec_sha256": "a" * 64,
            },
            "summary": {
                "total": 1,
                "relevant": 1,
                "irrelevant": 0,
                "uncertain": 0,
                "needs_human_review": 0,
            },
            "results": [
                {
                    "paper_id": 1,
                    "paper_task_id": "task-1",
                    "pmid": "123",
                    "input_sha256": "b" * 64,
                    "output": {
                        "schema_version": "1.0",
                        "decision": "relevant",
                        "relevance_score": 0.95,
                        "matched_domain_codes": ["gut_health"],
                        "reason": "直接研究肠道健康终点。",
                        "confidence": 0.95,
                        "warnings": [],
                        "needs_human_review": False,
                    },
                }
            ],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_results(self) -> None:
        self.results_path.write_text(
            json.dumps(self.results_package),
            encoding="utf-8",
        )

    def test_accepts_complete_valid_results(self) -> None:
        self._write_results()
        report = validate_screening_results(self.tasks_path, self.results_path)
        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["relevant"], 1)

    def test_rejects_unknown_domain_code(self) -> None:
        self.results_package["results"][0]["output"]["matched_domain_codes"] = ["unknown"]
        self._write_results()
        with self.assertRaisesRegex(ScreeningValidationError, "unknown code"):
            validate_screening_results(self.tasks_path, self.results_path)

    def test_rejects_uncertain_without_human_review(self) -> None:
        output = self.results_package["results"][0]["output"]
        output["decision"] = "uncertain"
        output["matched_domain_codes"] = []
        self.results_package["summary"] = {
            "total": 1,
            "relevant": 0,
            "irrelevant": 0,
            "uncertain": 1,
            "needs_human_review": 0,
        }
        self._write_results()
        with self.assertRaisesRegex(ScreeningValidationError, "requires human review"):
            validate_screening_results(self.tasks_path, self.results_path)

    def test_rejects_input_hash_mismatch(self) -> None:
        self.results_package["results"][0]["input_sha256"] = "c" * 64
        self._write_results()
        with self.assertRaisesRegex(ScreeningValidationError, "input SHA-256 mismatch"):
            validate_screening_results(self.tasks_path, self.results_path)


if __name__ == "__main__":
    unittest.main()
