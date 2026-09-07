from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/app.db"
    ncbi_api_key: str | None = None
    ncbi_email: str | None = None
    ncbi_tool: str = "ai_literature_intelligence"
    crossref_email: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            ncbi_api_key=os.getenv("NCBI_API_KEY") or None,
            ncbi_email=os.getenv("NCBI_EMAIL") or None,
            ncbi_tool=os.getenv("NCBI_TOOL", cls.ncbi_tool),
            crossref_email=os.getenv("CROSSREF_EMAIL") or None,
        )

