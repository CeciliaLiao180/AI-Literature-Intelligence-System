from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from literature_intelligence.config import Settings
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import ResearchDomain


def load_domain_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    domains = payload.get("domains")
    if not isinstance(domains, list) or not domains:
        raise ValueError("domain catalog must contain a non-empty domains list")
    required = {"code", "name_zh", "description", "is_active"}
    for index, domain in enumerate(domains):
        if not isinstance(domain, dict) or not required.issubset(domain):
            raise ValueError(f"domain catalog entry {index} is incomplete")
        if not all(
            isinstance(domain[key], str) and domain[key].strip()
            for key in ("code", "name_zh", "description")
        ):
            raise ValueError(f"domain catalog entry {index} has blank text fields")
        if not isinstance(domain["is_active"], bool):
            raise ValueError(f"domain catalog entry {index} has invalid is_active")
    return domains


def sync_domain_catalog(engine: Engine, path: Path) -> dict[str, int]:
    domains = load_domain_catalog(path)
    created = 0
    updated = 0
    with Session(engine) as session:
        for item in domains:
            domain = session.scalar(
                select(ResearchDomain).where(ResearchDomain.code == item["code"])
            )
            if domain is None:
                session.add(
                    ResearchDomain(
                        code=item["code"],
                        name_zh=item["name_zh"],
                        description=item["description"],
                        is_active=item["is_active"],
                    )
                )
                created += 1
            else:
                domain.name_zh = item["name_zh"]
                domain.description = item["description"]
                domain.is_active = item["is_active"]
                updated += 1
        session.commit()
    return {"created": created, "updated": updated, "total": len(domains)}


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Sync the domain catalog into the database.")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--config", type=Path, default=Path("config/domains.v1.json"))
    args = parser.parse_args()

    engine = create_database(args.database_url)
    try:
        result = sync_domain_catalog(engine, args.config)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
