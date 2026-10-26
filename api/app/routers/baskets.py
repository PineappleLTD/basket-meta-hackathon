from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.auth import CurrentUser
from app.db import SessionDep
from app.models import Basket, BasketNav, Holding, utcnow
from app.routers.common import HistoryLimit, history, owned_basket
from app.schemas import BasketIn, BasketOut, MarketOut, NavPoint, PositionOut, RebalancePlan, RebalanceResult, TradeOut
from app.services import baskets as svc

router = APIRouter(prefix="/baskets", tags=["baskets"])


def _basket_out(basket: Basket, valuation: svc.Valuation | None = None) -> BasketOut:
    valuation = valuation or svc.value_basket(basket)
    return BasketOut(
        id=basket.id,
        name=basket.name,
        description=basket.description,
        created_at=basket.created_at,
        rebalanced_at=basket.rebalanced_at,
        nav=valuation.nav,
        max_drift=valuation.max_drift,
        positions=[
            PositionOut(
                market=MarketOut.model_validate(pv.position.market),
                side=pv.position.side,
                target_weight=pv.position.target_weight,
                current_weight=pv.current_weight,
                drift=pv.drift,
                units=pv.position.units,
                price=pv.price,
                value=pv.value,
            )
            for pv in valuation.positions
        ],
    )


def _trade_out(trade: svc.Trade) -> TradeOut:
    return TradeOut(
        market_id=trade.position.market_id,
        side=trade.position.side,
        action=trade.action,
        price=trade.price,
        current_units=trade.current_units,
        target_units=trade.target_units,
        delta_units=trade.delta_units,
    )


@router.get("", response_model=list[BasketOut])
def list_baskets(session: SessionDep, user: CurrentUser):
    baskets = session.scalars(select(Basket).where(Basket.owner_id == user.id).order_by(Basket.created_at.desc()))
    return [_basket_out(b) for b in baskets]


@router.post("", response_model=BasketOut, status_code=201)
def create_basket(body: BasketIn, session: SessionDep, user: CurrentUser):
    if session.scalar(select(Basket.id).where(Basket.owner_id == user.id, Basket.name == body.name)) is not None:
        raise HTTPException(409, "You already have a basket with that name")
    try:
        positions = svc.build_positions(session, body.positions)
    except svc.BasketError as e:
        raise HTTPException(422, str(e)) from e

    basket = Basket(owner_id=user.id, name=body.name, description=body.description, positions=positions)
    valuation = svc.value_basket(basket)
    session.add_all([basket, BasketNav(basket=basket, ts=utcnow(), nav=valuation.nav)])
    session.commit()
    return _basket_out(basket, valuation)


@router.get("/{basket_id}", response_model=BasketOut)
def get_basket(basket_id: int, session: SessionDep, user: CurrentUser):
    return _basket_out(owned_basket(session, user, basket_id))


@router.get("/{basket_id}/nav", response_model=list[NavPoint])
def get_nav_history(
    basket_id: int,
    session: SessionDep,
    user: CurrentUser,
    since: datetime | None = None,
    limit: HistoryLimit = 1000,
):
    """NAV history, oldest first. `limit` keeps the most recent points."""
    owned_basket(session, user, basket_id)
    return history(session, BasketNav.basket_id, basket_id, since, limit)


@router.delete("/{basket_id}", status_code=204)
def delete_basket(basket_id: int, session: SessionDep, user: CurrentUser):
    basket = owned_basket(session, user, basket_id)
    if session.scalar(select(Holding.id).where(Holding.basket_id == basket.id).limit(1)) is not None:
        raise HTTPException(409, "Sell this basket's holdings before deleting it")
    session.delete(basket)
    session.commit()


@router.get("/{basket_id}/rebalance", response_model=RebalancePlan)
def preview_rebalance(
    basket_id: int,
    session: SessionDep,
    user: CurrentUser,
    threshold: Annotated[float, Query(gt=0, le=1)] = svc.DEFAULT_DRIFT_THRESHOLD,
):
    """Trades that would restore target weights at the current NAV. Changes nothing."""
    basket = owned_basket(session, user, basket_id)
    try:
        valuation, trades = svc.plan_rebalance(basket)
    except svc.BasketError as e:
        raise HTTPException(409, str(e)) from e
    return RebalancePlan(
        nav=valuation.nav,
        max_drift=valuation.max_drift,
        threshold=threshold,
        needs_rebalance=valuation.max_drift > threshold,
        trades=[_trade_out(t) for t in trades],
    )


@router.post("/{basket_id}/rebalance", response_model=RebalanceResult)
def rebalance(basket_id: int, session: SessionDep, user: CurrentUser):
    """Reset every position to its target weight at the current NAV."""
    basket = owned_basket(session, user, basket_id)
    try:
        trades = svc.apply_rebalance(basket)
    except svc.BasketError as e:
        raise HTTPException(409, str(e)) from e
    session.commit()
    return RebalanceResult(trades=[_trade_out(t) for t in trades], basket=_basket_out(basket))
