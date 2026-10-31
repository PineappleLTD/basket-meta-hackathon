"""Sync markets from Kalshi into the database, like POST /markets/sync but without logging in.

Also settles resolved markets and places automated trades that are due.

Run from api/: .venv/bin/python sync.py
Uses the same settings as the API (api/.env), so it writes to the same database.
"""

import logging
import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base, make_engine
from app.kalshi import KalshiClient
from app.main import make_brokers
from app.services.sync import refresh


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs every Kalshi request at INFO, thousands of lines on a sync.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    source = KalshiClient(
        settings.kalshi_min_volume,
        settings.kalshi_exclude_categories,
        requests_per_second=settings.kalshi_requests_per_second,
    )
    start = time.monotonic()
    with Session(engine, expire_on_commit=False) as session:
        counts = refresh(session, source, make_brokers(settings, source), settings.backfill_days)
    print(f"{counts} in {time.monotonic() - start:.0f}s")


if __name__ == "__main__":
    main()
