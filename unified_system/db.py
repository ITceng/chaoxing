from __future__ import annotations

from flask import current_app, g
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def init_database(app) -> None:
    url = app.config["DATABASE_URL"]
    options = {"pool_pre_ping": True, "future": True}
    parsed_url = make_url(url)
    if parsed_url.get_backend_name() == "sqlite" and parsed_url.database not in (None, "", ":memory:"):
        Path(parsed_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if url.endswith(":memory:") or url.endswith("/:memory:"):
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    app.extensions["unified_engine"] = engine
    app.extensions["unified_session_factory"] = sessionmaker(
        bind=engine, class_=Session, expire_on_commit=False, autoflush=False
    )


def get_db() -> Session:
    if "unified_db" not in g:
        factory = current_app.extensions["unified_session_factory"]
        g.unified_db = factory()
    return g.unified_db


def close_db(_error=None) -> None:
    session = g.pop("unified_db", None)
    if session is not None:
        if session.in_transaction():
            session.rollback()
        session.close()
