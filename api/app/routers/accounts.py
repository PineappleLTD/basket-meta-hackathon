from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import object_session
from sqlalchemy.orm.exc import StaleDataError

from app.arb_models import lock_for
from app.auth import CurrentUser
from app.brokers import Broker, to_cents
from app.db import SessionDep
from app.models import (
    Account,
    AccountMode,
    AccountValue,
    Basket,
    BasketOrder,
    ConditionalOrder,
    ConditionalStatus,
    StakeValue,
    User,
    as_utc,
    utcnow,
)
from app.routers.common import HistoryLimit, history, owned_basket
from app.schemas import (
    AccountOut,
    AutomationIn,
    AutomationOut,
    BasketHoldingsOut,
    BasketOrderOut,
    BuyIn,
    ConditionalOrderIn,
    ConditionalOrderOut,
    EquityPoint,
    HoldingOut,
    MarketOut,
    PlannedOrderOut,
    RebalanceIn,
    SellIn,
    SkippedLegOut,
    StakePoint,
    TradePreview,
    TradeResult,
)
from app.services import trading as svc
from app.services.automation import triggered
from app.services.baskets import value_basket

router = APIRouter(prefix="/accounts/{mode}", tags=["trading"])

_RACE = "Your account changed while this order was being placed. Nothing was traded; try again."


def _broker(request: Request, mode: AccountMode) -> Broker:
    broker = request.app.state.brokers.get(mode)
    if broker is None:
        raise HTTPException(501, f"{mode.capitalize()} trading isn't available yet")
    return broker


BrokerDep = Annotated[Broker, Depends(_broker)]


def _account(request: Request, mode: AccountMode, session: SessionDep, user: CurrentUser, _: BrokerDep) -> Account:
    return svc.get_account(session, user, mode, request.app.state.settings.paper_starting_cash)


AccountDep = Annotated[Account, Depends(_account)]


def _not_locked(basket: Basket) -> Basket:
    """Automations trade by weight, which would break a locked arbitrage's matched legs."""
    if lock_for(object_session(basket), basket.id):
        raise HTTPException(409, "This basket is a locked arbitrage: it's held to settlement, so automations are off")
    return basket


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b - 1 if a is not None and b else None


def _basket_holdings_out(b: svc.BasketHoldings) -> BasketHoldingsOut:
    holdings = [
        HoldingOut(
            market=MarketOut.model_validate(hv.holding.market),
            side=hv.holding.side,
            contracts=hv.holding.contracts,
            avg_cost=hv.holding.cost / hv.holding.contracts,
            cost=hv.holding.cost,
            price=hv.price,
            value=hv.value,
            liquidation_value=hv.liquidation_value,
            unrealized_pnl=hv.value - hv.holding.cost,
            weight=hv.weight,
            target_weight=hv.target_weight,
            drift=hv.weight - hv.target_weight,
        )
        for hv in b.holdings
    ]
    stake = b.stake
    opened_nav = stake.opened_nav if stake else None
    return BasketHoldingsOut(
        basket_id=b.basket.id,
        basket_name=b.basket.name,
        nav=b.nav,
        value=b.value,
        liquidation_value=b.liquidation_value,
        cost=b.cost,
        unrealized_pnl=b.value - b.cost,
        realized_pnl=stake.realized_pnl if stake else 0.0,
        max_drift=b.max_drift,
        shares=stake.shares if stake else 0.0,
        share_value=b.share_value,
        opened_nav=opened_nav,
        opened_at=stake.opened_at if stake else None,
        holding_return=_ratio(b.share_value, opened_nav),
        index_return=_ratio(b.nav, opened_nav) if b.share_value is not None else None,
        holdings=holdings,
        cash=b.cash,
    )


def _account_out(account: Account, broker: Broker | None = None) -> AccountOut:
    valuation = svc.value_account(account, broker)
    return AccountOut(
        mode=account.mode,
        cash=account.cash,
        available_cash=svc.spendable_cash(account) / 100,
        starting_cash=account.starting_cash,
        holdings_value=valuation.holdings_value,
        equity=valuation.equity,
        liquidation_equity=valuation.liquidation_equity,
        pnl=valuation.equity - account.starting_cash,
        realized_pnl=valuation.realized_pnl,
        created_at=account.created_at,
        reset_at=account.reset_at,
        baskets=[_basket_holdings_out(b) for b in valuation.baskets],
    )


def _skipped(plan: svc.Plan) -> list[SkippedLegOut]:
    return [SkippedLegOut(market_id=s.market_id, side=s.side, reason=s.reason) for s in plan.skipped]


def _preview(plan: svc.Plan, account: Account) -> TradePreview:
    orders = [
        PlannedOrderOut(
            market_id=o.request.market.id,
            side=o.request.side,
            action=o.request.action,
            count=o.request.count,
            price=o.avg_price,
            best_price=o.best_price,
            limit_price=o.request.limit_price,
            price_impact=abs(o.avg_price - o.best_price) * o.request.count,
            fee=o.fee_cents / 100,
            cash_delta=o.cash_cents / 100,
        )
        for o in plan.orders
    ]
    return TradePreview(
        kind=plan.kind,
        orders=orders,
        skipped=_skipped(plan),
        fees=sum(o.fee for o in orders),
        price_impact=sum(o.price_impact for o in orders),
        cash_delta=plan.cash_delta_cents / 100,
        cash_after=(svc.spendable_cash(account) + plan.cash_delta_cents - plan.reserve_cash_cents) / 100,
        basket_cash_delta=plan.reserve_cash_cents / 100,
    )


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
        return _preview(plan, account)

    try:
        record = svc.execute(session, account, basket, plan, broker)
        session.commit()
    except svc.TradingError as e:
        session.rollback()
        raise HTTPException(409, str(e)) from e
    except StaleDataError as e:
        # Another trade on this account committed after this one was planned.
        session.rollback()
        raise HTTPException(409, _RACE) from e
    return TradeResult(
        basket_order=BasketOrderOut.model_validate(record), skipped=_skipped(plan), account=_account_out(account, broker)
    )


@router.get("", response_model=AccountOut)
def get_account(account: AccountDep, broker: BrokerDep):
    """Cash, equity, and holdings grouped by basket. Opens the account on first use."""
    return _account_out(account, broker)


@router.post("/reset", response_model=AccountOut)
def reset_account(mode: AccountMode, request: Request, account: AccountDep, broker: BrokerDep, session: SessionDep):
    """Clear every holding, order, automation and history point, and restore the starting cash. Paper only."""
    if mode != AccountMode.PAPER:
        raise HTTPException(409, "Only paper accounts can be reset")
    svc.reset_account(session, account, request.app.state.settings.paper_starting_cash)
    session.commit()
    return _account_out(account, broker)


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
    """Put `amount` dollars into the basket at its current weights, walking the asks. Fees come out of the amount."""
    return _run(
        session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_buy(account, b, body.amount, broker)
    )


@router.post("/baskets/{basket_id}/sell", response_model=TradePreview | TradeResult)
def sell_basket(
    basket_id: int, body: SellIn, account: AccountDep, broker: BrokerDep, session: SessionDep, user: CurrentUser
):
    """Sell `fraction` of every holding in the basket into the bids. Legs that can't trade stay held."""
    return _run(
        session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_sell(account, b, body.fraction, broker)
    )


@router.post("/baskets/{basket_id}/rebalance", response_model=TradePreview | TradeResult)
def rebalance_holdings(
    basket_id: int, body: RebalanceIn, account: AccountDep, broker: BrokerDep, session: SessionDep, user: CurrentUser
):
    """Reset the basket's index to its targets and trade the holdings to match, sells first."""
    return _run(session, user, account, broker, basket_id, body.dry_run, lambda b: svc.plan_rebalance(account, b, broker))


@router.get("/baskets/{basket_id}/history", response_model=list[StakePoint])
def get_stake_history(
    basket_id: int,
    account: AccountDep,
    session: SessionDep,
    user: CurrentUser,
    since: datetime | None = None,
    limit: HistoryLimit = 1000,
):
    """The holding's value and value per share (after every trade and sync), with the NAV at each point."""
    owned_basket(session, user, basket_id)
    stake = svc.find_stake(account, basket_id)
    return [] if stake is None else history(session, StakeValue.stake_id, stake.id, since, limit)


def _automation_out(account: Account, basket_id: int) -> AutomationOut:
    stake = svc.find_stake(account, basket_id)
    if stake is None:
        return AutomationOut(
            auto_rebalance_drift=None, recurring_amount=None, recurring_days=None, next_recurring_at=None, note=None
        )
    return AutomationOut(
        auto_rebalance_drift=stake.auto_rebalance_drift,
        recurring_amount=None if stake.recurring_amount_cents is None else stake.recurring_amount_cents / 100,
        recurring_days=stake.recurring_days,
        next_recurring_at=stake.next_recurring_at,
        note=stake.automation_note,
    )


@router.get("/baskets/{basket_id}/automation", response_model=AutomationOut)
def get_automation(basket_id: int, account: AccountDep, session: SessionDep, user: CurrentUser):
    owned_basket(session, user, basket_id)
    return _automation_out(account, basket_id)


@router.put("/baskets/{basket_id}/automation", response_model=AutomationOut)
def set_automation(basket_id: int, body: AutomationIn, account: AccountDep, session: SessionDep, user: CurrentUser):
    """Replace the basket's automations. They run on each sync; omitted fields turn off."""
    basket = _not_locked(owned_basket(session, user, basket_id))
    stake = svc.get_stake(account, basket)
    stake.auto_rebalance_drift = body.auto_rebalance_drift
    if body.recurring_amount is None:
        stake.recurring_amount_cents = stake.recurring_days = stake.next_recurring_at = None
    else:
        changed = (
            stake.recurring_amount_cents is None
            or stake.recurring_days != body.recurring_days
            or body.recurring_start is not None
        )
        stake.recurring_amount_cents = to_cents(body.recurring_amount, up=False)
        stake.recurring_days = body.recurring_days
        # Keep the schedule unless it was just set up, its interval changed, or a start was given.
        if changed:
            stake.next_recurring_at = as_utc(body.recurring_start) if body.recurring_start else utcnow()
    stake.automation_note = None
    try:
        session.commit()
    except StaleDataError as e:
        session.rollback()
        raise HTTPException(409, _RACE) from e
    return _automation_out(account, basket_id)


@router.get("/conditional-orders", response_model=list[ConditionalOrderOut])
def list_conditional_orders(
    account: AccountDep, session: SessionDep, basket_id: int | None = None, status: ConditionalStatus | None = None
):
    """Newest first."""
    stmt = select(ConditionalOrder).where(ConditionalOrder.account_id == account.id)
    if basket_id is not None:
        stmt = stmt.where(ConditionalOrder.basket_id == basket_id)
    if status is not None:
        stmt = stmt.where(ConditionalOrder.status == status)
    return session.scalars(stmt.order_by(ConditionalOrder.created_at.desc(), ConditionalOrder.id.desc())).all()


@router.post("/baskets/{basket_id}/conditional-orders", response_model=ConditionalOrderOut, status_code=201)
def create_conditional_order(
    basket_id: int, body: ConditionalOrderIn, account: AccountDep, session: SessionDep, user: CurrentUser
):
    """Buy or sell once the basket's NAV is at or past `nav`, checked on each sync.

    A limit buy is a buy at_or_below; a stop-loss is a sell at_or_below; a take-profit is a
    sell at_or_above. 409 if the NAV is already there: place a regular order instead.
    """
    basket = _not_locked(owned_basket(session, user, basket_id))
    nav = value_basket(basket).nav
    if triggered(body.condition, body.nav, nav):
        raise HTTPException(409, f"The NAV is already {nav:.2f}; place a regular order instead")
    order = ConditionalOrder(account_id=account.id, basket_id=basket.id, **body.model_dump())
    session.add(order)
    session.commit()
    return order


@router.delete("/conditional-orders/{order_id}", status_code=204)
def cancel_conditional_order(order_id: int, account: AccountDep, session: SessionDep):
    order = session.get(ConditionalOrder, order_id)
    if order is None or order.account_id != account.id:
        raise HTTPException(404, "Conditional order not found")
    if order.status != ConditionalStatus.OPEN:
        raise HTTPException(409, f"This order is already {order.status}")
    order.status = ConditionalStatus.CANCELLED
    session.commit()
