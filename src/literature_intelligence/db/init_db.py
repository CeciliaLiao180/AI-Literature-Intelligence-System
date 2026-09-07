from __future__ import annotations

import argparse

from sqlalchemy import inspect

from literature_intelligence.config import Settings
from literature_intelligence.db.base import create_database


def initialize(database_url: str) -> list[str]:
    engine = create_database(database_url)
    try:
        return sorted(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the MVP database.")
    parser.add_argument("--database-url", default=Settings.from_env().database_url)
    args = parser.parse_args()
    tables = initialize(args.database_url)
    print(f"Initialized {len(tables)} tables")
    for table in tables:
        print(table)


if __name__ == "__main__":
    main()

