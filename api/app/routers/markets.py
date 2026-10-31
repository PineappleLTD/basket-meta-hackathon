from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.auth import CurrentUser
from app.db import SessionDep
from app.models import Market, MarketPrice
from app.routers.common import HistoryLimit, get_or_404, history
from app.schemas import MarketOut, MarketPage, PricePoint, SyncCounts
from app.services.sync import refresh

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=MarketPage)
def list_markets(
    session: SessionDep,
    q: Annotated[str | None, Query(description="Case-insensitive match on the question")] = None,
    is_open: bool | None = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = select(Market)
    if q:
        stmt = stmt.where(Market.question.ilike(f"%{q}%"))
    if is_open is not None:
        stmt = stmt.where(Market.is_open == is_open)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = session.scalars(
        stmt.order_by(Market.volume.desc(), Market.id).limit(limit).offset(offset)
    ).all()
    return MarketPage(total=total, items=items)


@router.get("/{market_id}", response_model=MarketOut)
def get_market(market_id: int, session: SessionDep):
    return get_or_404(session, Market, market_id)


@router.get("/{market_id}/prices", response_model=list[PricePoint])
def get_prices(market_id: int, session: SessionDep, since: datetime | None = None, limit: HistoryLimit = 1000):
    """YES price history, oldest first. `limit` keeps the most recent points."""
    get_or_404(session, Market, market_id)
    return history(session, MarketPrice.market_id, market_id, since, limit)


@router.post("/sync", response_model=SyncCounts)
def sync(request: Request, session: SessionDep, _user: CurrentUser):
    """Pull markets from Kalshi, upsert them, settle resolved ones, record prices and NAVs, and
    place automated trades that are due. Requires sign-in.

    A live sync pages through every open event, so it takes around half a minute. The API
    also syncs on its own every PMETF_SYNC_INTERVAL_MINUTES.
    """
    state = request.app.state
    if not state.sync_lock.acquire(blocking=False):
        raise HTTPException(409, "A sync is already running")
    try:
        return refresh(session, state.market_source, state.brokers, state.settings.backfill_days)
    finally:
        state.sync_lock.release()
