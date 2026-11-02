import logging
import time
from datetime import datetime
from typing import Annotated

from fastapi import HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import Basket, User, as_utc
from app.services.sync import refresh

log = logging.getLogger(__name__)

HistoryLimit = Annotated[int, Query(ge=1, le=10_000)]


def get_or_404[T](session: Session, model: type[T], id: int) -> T:
    obj = session.get(model, id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def owned_basket(session: Session, user: User, basket_id: int) -> Basket:
    """Another user's basket is reported missing, so ids don't reveal what exists."""
    basket = session.get(Basket, basket_id)
    if basket is None or basket.owner_id != user.id:
        raise HTTPException(404, "Basket not found")
    return basket


def history(session: Session, owner: InstrumentedAttribute, owner_id: int, since: datetime | None, limit: int):
    """Rows of `owner`'s time-series table for one owner, oldest first. `limit` keeps the most recent."""
    model = owner.class_
    stmt = select(model).where(owner == owner_id)
    if since is not None:
        stmt = stmt.where(model.ts >= as_utc(since))
    return session.scalars(stmt.order_by(model.ts.desc()).limit(limit)).all()[::-1]


def sync_now(state) -> dict[str, int]:
    """Sync and run automations, noting when for sync_if_stale. The caller holds state.sync_lock."""
    started = time.monotonic()
    with state.sessionmaker() as session:
        counts = refresh(session, state.market_source, state.brokers, state.settings.backfill_days)
    state.synced_at = started
    return counts


def sync_if_stale(state) -> None:
    """Sync when no sync has finished in the last sync_max_age_minutes (or since startup).
    Waits out a sync already running, then checks again, so callers arriving together sync once.
    A failed sync is logged and the caller carries on with the markets already stored."""
    max_age = state.settings.sync_max_age_minutes * 60

    def stale() -> bool:
        return state.synced_at is None or time.monotonic() - state.synced_at > max_age

    if max_age <= 0 or not stale():
        return
    with state.sync_lock:
        if not stale():
            return
        try:
            log.info("Synced stale markets: %s", sync_now(state))
        except Exception:
            log.warning("Couldn't sync stale markets; using the last sync's", exc_info=True)
