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
    """Sync and run automations, noting when for sync_fresh. The caller holds state.sync_lock."""
    started = time.monotonic()
    with state.sessionmaker() as session:
        counts = refresh(session, state.market_source, state.brokers, state.settings.backfill_days)
    state.synced_at = started
    return counts


def sync_fresh(state) -> None:
    """Sync unless one has started since this call did. Waits out a sync already running, then
    syncs again, so callers arriving together sync once and none sees markets from before it
    arrived. A failed sync is logged and the caller carries on with the markets already stored."""
    arrived = time.monotonic()
    with state.sync_lock:
        if state.synced_at is not None and state.synced_at >= arrived:
            return
        try:
            log.info("Synced markets: %s", sync_now(state))
        except Exception:
            log.warning("Couldn't sync markets; using the last sync's", exc_info=True)
