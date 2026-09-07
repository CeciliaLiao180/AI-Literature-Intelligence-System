from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import create_engine


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str, **kwargs: Any) -> Engine:
    _ensure_sqlite_parent(database_url)
    connect_args = dict(kwargs.pop("connect_args", {}))
    if database_url.startswith("sqlite"):
        connect_args.setdefault("check_same_thread", False)
    engine = create_engine(database_url, connect_args=connect_args, **kwargs)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_database(database_url: str) -> Engine:
    from literature_intelligence.db import models  # noqa: F401

    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    return engine

