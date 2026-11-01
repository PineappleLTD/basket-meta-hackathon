"""Copy every table from the SQLite databases (data/app.db + data/shared.db) into Postgres.

Run from api/: .venv/bin/python migrate_to_postgres.py
Writes to PMETF_DATABASE_URL (api/.env), which must be a postgresql:// URI, e.g. Supabase's
session pooler. Creates the tables if needed, refuses to run if any already has rows, and
copies everything in one transaction, so a failure leaves Postgres as it was.
"""

import sys
import time
from enum import Enum

from sqlalchemy import func, select

from app import models  # noqa: F401  (registers the tables on Base)
from app.config import API_DIR, Settings
from app.db import Base, init_db, make_engine


def main() -> None:
    settings = Settings()
    dst = make_engine(settings.database_url)
    if dst.dialect.name != "postgresql":
        sys.exit(f"PMETF_DATABASE_URL must point at Postgres, not {dst.dialect.name}")
    src = make_engine(f"sqlite:///{API_DIR / 'data' / 'app.db'}", str(API_DIR / "data" / "shared.db"))
    init_db(dst)

    tables = Base.metadata.sorted_tables
    with src.connect() as s, dst.connect() as d:
        filled = [t.fullname for t in tables if d.scalar(select(func.count()).select_from(t))]
        if filled:
            sys.exit(f"Postgres already has rows in {', '.join(filled)}; empty them first")

        raw = d.connection.driver_connection
        for table in tables:
            start = time.monotonic()
            cols = ", ".join(f'"{c.name}"' for c in table.columns)
            n = 0
            with raw.cursor() as cur:
                with cur.copy(f"COPY {table.fullname} ({cols}) FROM STDIN") as copy:
                    for row in s.execution_options(yield_per=10_000).execute(select(table)):
                        copy.write_row([v.value if isinstance(v, Enum) else v for v in row])
                        n += 1
                # Rows came in with their ids, so move each id sequence past them.
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table.fullname}', 'id'), "
                    f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table.fullname}"
                )
            print(f"{table.fullname}: {n} rows in {time.monotonic() - start:.1f}s")
        d.commit()
    print("Done")


if __name__ == "__main__":
    main()
