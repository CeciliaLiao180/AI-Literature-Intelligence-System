from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from literature_intelligence.pipeline.site_artifact import validate_site_artifact
from literature_intelligence.pipeline.static_site import (
    build_cumulative_site_data,
    generate_site,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SiteArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.site_dir = Path(self.temp_dir.name) / "site"
        data = build_cumulative_site_data(
            database_url=(
                f"sqlite:///{(PROJECT_ROOT / 'data' / 'gut_health_mvp.db').as_posix()}"
            ),
            site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
            journal_metrics_path=PROJECT_ROOT / "config" / "journal_metrics.v1.json",
        )
        generate_site(data, self.site_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validates_complete_preview_artifact(self) -> None:
        result = validate_site_artifact(
            self.site_dir,
            required_publication_status="preview",
        )
        self.assertEqual(result["publication_status"], "preview")
        self.assertEqual(result["papers"], 4)
        self.assertTrue((self.site_dir / ".nojekyll").exists())

    def test_rejects_preview_when_published_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 'published'"):
            validate_site_artifact(
                self.site_dir,
                required_publication_status="published",
            )

    def test_rejects_tampered_file(self) -> None:
        (self.site_dir / "index.html").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate_site_artifact(self.site_dir)

    def test_rejects_unexpected_file(self) -> None:
        (self.site_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected=.*unexpected.txt"):
            validate_site_artifact(self.site_dir)

    def test_rejects_manifest_without_top_level_index(self) -> None:
        manifest_path = self.site_dir / "data" / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].pop("index.html")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.site_dir / "index.html").unlink()
        with self.assertRaisesRegex(ValueError, "no top-level index.html"):
            validate_site_artifact(self.site_dir)
