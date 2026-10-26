from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.auth import CurrentUser
from app.brokers import Broker
from app.db import SessionDep
from app.models import Account, AccountMode, AccountValue, Basket, BasketOrder, User
from app.routers.common import HistoryLimit, history, owned_basket
from app.schemas import (
    AccountOut,
    BasketHoldingsOut,
    BasketOrderOut,
    BuyIn,
    EquityPoint,
    HoldingOut,
    MarketOut,
    PlannedOrderOut,
    RebalanceIn,
    SellIn,
    SkippedLegOut,
    TradePreview,
    TradeResult,
)
from app.services import trading as svc

router = APIRouter(prefix="/accounts/{mode}", tags=["trading"])


def _broker(request: Request, mode: AccountMode) -> Broker:
    broker = request.app.state.brokers.get(mode)
    if broker is None:
        raise HTTPException(501, f"{mode.capitalize()} trading isn't available yet")
    return broker


BrokerDep = Annotated[Broker, Depends(_broker)]


def _account(request: Request, mode: AccountMode, session: SessionDep, user: CurrentUser, _: BrokerDep) -> Account:
    return svc.get_account(session, user, mode, request.app.state.settings.paper_starting_cash)


AccountDep = Annotated[Account, Depends(_account)]


def _account_out(account: Account) -> AccountOut:
    valuation = svc.value_account(account)
    baskets = []
    for b in valuation.baskets:
        holdings = [
            HoldingOut(
                market=MarketOut.model_validate(hv.holding.market),
                side=hv.holding.side,
                contracts=hv.holding.contracts,
                avg_cost=hv.holding.cost / hv.holding.contracts,
                cost=hv.holding.cost,
                price=hv.price,
                value=hv.value,
                unrealized_pnl=hv.value - hv.holding.cost,
                weight=hv.weight,
                target_weight=hv.target_weight,
                drift=hv.weight - hv.target_weight,
            )
            for hv in b.holdings
        ]
        baskets.append(
            BasketHoldingsOut(
                basket_id=b.basket.id,
                basket_name=b.basket.name,
                value=b.value,
                cost=b.cost,
                unrealized_pnl=b.value - b.cost,
                max_drift=max((abs(h.drift) for h in holdings), default=0.0),
                holdings=holdings,
            )
        )
    return AccountOut(
        mode=account.mode,
        cash=account.cash,
        starting_cash=account.starting_cash,
        holdings_value=valuation.holdings_value,
        equity=valuation.equity,
        pnl=valuation.equity - account.starting_cash,
        created_at=account.created_at,
        reset_at=account.reset_at,
        baskets=baskets,
    )


def _skipped(plan: svc.Plan) -> list[SkippedLegOut]:
    return [SkippedLegOut(market_id=s.market_id, side=s.side, reason=s.reason) for s in plan.skipped]


def _run(
    session: SessionDep,
    user: User,
    account: Account,
    broker: Broker,
    basket_id: int,
    dry_run: bool,
    make_plan: Callable[[Basket], svc.Plan],
) -> TradePreview | TradeResult:
    """Plan against the user's basket; return the plan when `dry_run`, else place it."""
    basket = owned_basket(session, user, basket_id)
    try:
        plan = make_plan(basket)
    except svc.TradingError as e:
        raise HTTPException(409, str(e)) from e

    if dry_run:
        cash_delta = svc.cash_delta(plan.orders, broker)
        return TradePreview(
            kind=plan.kind,
            orders=[
                PlannedOrderOut(
                    market_id=o.market.id,
                    side=o.side,
                    action=o.action,
                    count=o.count,
                    price=o.limit_price,
                    fee=broker.fee(o.count, o.limit_price),
                    cash_delta=svc.order_cash(o, broker),
                )
                for o in plan.orders
            ],
            skipped=_skipped(plan),
            cash_delta=cash_delta,
            cash_after=account.cash + cash_delta,
        )

    record = svc.execute(session, account, basket, plan, broker)
    session.commit()
    return TradeResult(
        basket_order=BasketOrderOut.model_validate(record), skipped=_skipped(plan), account=_account_out(account)
    )


@router.get("", response_model=AccountOut)
def get_account(account: AccountDep):
    """Cash, equity, and holdings grouped by basket. Opens the account on first use."""
    return _account_out(account)


@router.post("/reset", response_model=AccountOut)
def reset_account(mode: AccountMode, request: Request, account: AccountDep, session: SessionDep):
    """Clear every holding and order and restore the starting cash. Paper only."""
    if mode != AccountMode.PAPER:
        raise HTTPException(409, "Only paper accounts can be reset")
    svc.reset_account(session, account, request.app.state.settings.paper_starting_cash)
    session.commit()
    return _account_out(account)


@router.get("/equity", response_model=list[EquityPoint])
def get_equity_history(
    account: AccountDep, session: SessionDep, since: datetime | None = None, limit: HistoryLimit = 1000
):
    """Equity history (opening, every trade, every sync), oldest first. `limit` keeps the most recent."""
    return history(session, AccountValue.account_id, account.id, since, limit)


@router.get("/orders", response_model=list[BasketOrderOut])
def list_orders(
    account: AccountDep,
    session: SessionDep,
    basket_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Basket orders and the orders they placed, newest first."""
    stmt = select(BasketOrder).where(BasketOrder.account_id == account.id)
    if basket_id is not None:
        stmt = stmt.where(BasketOrder.basket_id == basket_id)
    return session.scalars(stmt.order_by(BasketOrder.created_at.desc(), BasketOrder.id.desc()).limit(limit)).all()


@router.post("/baskets/{basket_id}/buy", response_model=TradePreview | TradeResult)
def buy_basket(
    basket_id: int, body: BuyIn, account: AccountDep, broker: BrokerDep, session: SessionDep, user: CurrentUser
):
    """Put `amount` dollars into the basket at the asks, split by target weight. Fees come out of the amount."""
    return _run(
        session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_buy(account, b, body.amount, broker)
    )


@router.post("/baskets/{basket_id}/sell", response_model=TradePreview | TradeResult)
def sell_basket(
    basket_id: int, body: SellIn, account: AccountDep, broker: BrokerDep, session: SessionDep, user: CurrentUser
):
    """Sell `fraction` of every holding in the basket at the bids. Legs with no bid stay held."""
    return _run(
        session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_sell(account, b, body.fraction, broker)
    )


@router.post("/baskets/{basket_id}/rebalance", response_model=TradePreview | TradeResult)
def rebalance_holdings(
    basket_id: int, body: RebalanceIn, account: AccountDep, broker: BrokerDep, session: SessionDep, user: CurrentUser
):
    """Trade the basket's holdings back to target weights at their market value, sells first."""
    return _run(session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_rebalance(account, b, broker))
