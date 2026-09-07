from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import ResearchDomain
from literature_intelligence.db.sync_domains import sync_domain_catalog


class DomainCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.engine = create_database(f"sqlite:///{(root / 'domains.db').as_posix()}")
        self.catalog = root / "domains.json"

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _write_catalog(self, description: str) -> None:
        self.catalog.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "domains": [
                        {
                            "code": "gut_health",
                            "name_zh": "肠道健康",
                            "description": description,
                            "is_active": True,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_creates_and_updates_domain_from_catalog(self) -> None:
        self._write_catalog("定义一")
        self.assertEqual(
            sync_domain_catalog(self.engine, self.catalog),
            {"created": 1, "updated": 0, "total": 1},
        )
        self._write_catalog("定义二")
        self.assertEqual(
            sync_domain_catalog(self.engine, self.catalog),
            {"created": 0, "updated": 1, "total": 1},
        )
        with Session(self.engine) as session:
            domain = session.scalar(select(ResearchDomain))
            self.assertEqual(domain.description, "定义二")
            self.assertTrue(domain.is_active)


if __name__ == "__main__":
    unittest.main()
