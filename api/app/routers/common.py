from datetime import datetime
from typing import Annotated

from fastapi import HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import Basket, User, as_utc

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
