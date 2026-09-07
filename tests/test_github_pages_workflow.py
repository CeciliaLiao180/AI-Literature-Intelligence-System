from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GitHubPagesWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "deploy-pages.yml"
        ).read_text(encoding="utf-8")

    def test_uses_official_pages_actions_and_minimum_permissions(self) -> None:
        for value in (
            "actions/checkout@v6",
            "actions/configure-pages@v5",
            "actions/upload-pages-artifact@v4",
            "actions/deploy-pages@v4",
            "contents: read",
            "pages: write",
            "id-token: write",
            "path: site",
        ):
            self.assertIn(value, self.workflow)

    def test_is_manual_only_and_requires_published_artifact(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertIn("--require-publication-status published", self.workflow)


if __name__ == "__main__":
    unittest.main()
