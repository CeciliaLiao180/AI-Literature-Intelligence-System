from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from literature_intelligence.pipeline.codex_mvp_preview import (
    MVPValidationError,
    render_preview,
    validate_mvp_results,
)


BATCH_ID = "b031d6db-5d87-4506-96c6-5364ad63606a"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_PACKAGE = PROJECT_ROOT / "data" / "codex_tasks" / f"{BATCH_ID}_gut_health.json"
SCREENING_RESULTS = (
    PROJECT_ROOT / "data" / "codex_results" / f"{BATCH_ID}_gut_health_screening.json"
)
MVP_RESULTS = PROJECT_ROOT / "data" / "codex_results" / f"{BATCH_ID}_gut_health_mvp.json"


class CodexMVPPreviewTests(unittest.TestCase):
    def test_real_mvp_results_pass_all_contract_checks(self) -> None:
        report = validate_mvp_results(TASK_PACKAGE, SCREENING_RESULTS, MVP_RESULTS)
        self.assertTrue(report["valid"])
        self.assertEqual(
            report["summary"],
            {
                "retrieved": 7,
                "included": 4,
                "excluded": 2,
                "manual_review": 1,
                "quality_pass": 4,
                "quality_review": 0,
                "quality_reject": 0,
            },
        )

    def test_renders_complete_standalone_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "preview.html"
            result = render_preview(
                TASK_PACKAGE,
                SCREENING_RESULTS,
                MVP_RESULTS,
                output,
            )
            rendered = output.read_text(encoding="utf-8")

        self.assertEqual(result["summary"]["included"], 4)
        self.assertEqual(rendered.count('<article class="paper-card">'), 4)
        for heading in (
            "本期文献 4 篇",
            "领域分类",
            "核心摘要",
            "实验设计",
            "核心结果",
            "研究局限",
            "研发启示",
            "查看原文证据",
            "未纳入本期",
            "待人工审核（1）",
            "尚未正式发布",
        ):
            self.assertIn(heading, rendered)
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/42588089/", rendered)
        self.assertIn("https://doi.org/10.1002/biot.70250", rendered)

    def test_rejects_evidence_that_is_not_an_exact_source_quote(self) -> None:
        payload = json.loads(MVP_RESULTS.read_text(encoding="utf-8"))
        payload["items"][0]["analysis"]["key_findings"][0]["evidence"][0][
            "quote"
        ] += " not in source"
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_results = Path(temp_dir) / "invalid.json"
            invalid_results.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MVPValidationError, "exact source substring"):
                validate_mvp_results(
                    TASK_PACKAGE,
                    SCREENING_RESULTS,
                    invalid_results,
                )

    def test_rejects_report_link_outside_verified_sources(self) -> None:
        payload = json.loads(MVP_RESULTS.read_text(encoding="utf-8"))
        payload["items"][0]["report"]["source_links"][0] = "https://example.com/unverified"
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_results = Path(temp_dir) / "invalid.json"
            invalid_results.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MVPValidationError, "verified links"):
                validate_mvp_results(
                    TASK_PACKAGE,
                    SCREENING_RESULTS,
                    invalid_results,
                )


if __name__ == "__main__":
    unittest.main()
