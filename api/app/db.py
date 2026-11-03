from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str, shared_database: str = ":memory:") -> Engine:
    """`shared_database` is a SQLite file attached to every connection as schema `shared`.
    Postgres (e.g. Supabase) keeps everything in one database and ignores it."""
    url = make_url(database_url)
    # Accept a plain postgresql:// URI, as Supabase shows it.
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    kwargs: dict = {}
    if url.get_backend_name() == "postgresql":
        # The Supabase pooler drops idle connections.
        kwargs["pool_pre_ping"] = True
    if url.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.database in (None, "", ":memory:"):
            # Share one connection so every session sees the same in-memory DB.
            kwargs["poolclass"] = StaticPool
        else:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        if shared_database != ":memory:":
            Path(shared_database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("ATTACH DATABASE ? AS shared", (shared_database,))

    if url.get_backend_name() == "postgresql":

        @event.listens_for(engine, "connect")
        def _utc(dbapi_conn, _record):
            # Columns are timestamp without time zone, holding UTC. Postgres converts aware
            # datetimes to the session's zone on the way in, so make that zone UTC.
            dbapi_conn.execute("SET TIME ZONE 'UTC'")
            dbapi_conn.commit()

    return engine


def init_db(engine: Engine) -> None:
    """Create any missing tables. On Postgres, also the `shared` schema, and row level
    security on every table: Supabase's Data API exposes tables to its anon key, and RLS
    with no policies shuts that out while the API (connecting as the tables' owner) still
    has full access."""
    from app import news_models  # register additive feature tables for CLI entry points too
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS shared"))
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            # ALTER TABLE waits for an exclusive lock, behind any open transaction such as a
            # running sync, so only run it on tables that don't have RLS yet.
            secured = set(
                conn.scalars(
                    text(
                        "SELECT n.nspname || '.' || c.relname FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relrowsecurity"
                    )
                )
            )
            for table in Base.metadata.sorted_tables:
                if f"{table.schema or 'public'}.{table.name}" not in secured:
                    conn.execute(text(f"ALTER TABLE {table.fullname} ENABLE ROW LEVEL SECURITY"))


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
