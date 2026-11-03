"""Trades that place themselves, checked after every sync.

- Conditional orders fire once the basket's NAV is at or past their level.
- Recurring buys put a fixed amount in every few days.
- Auto-rebalancing trades a stake back to target once any holding drifts past its threshold.

Each action commits on its own, so one that fails (not enough cash, nothing to trade)
leaves a note and doesn't stop the rest. One that loses a race with a trade the user
placed at the same moment is rolled back and tried again on the next sync.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.arb_models import lock_for
from app.brokers import Broker
from app.models import (
    AccountMode,
    Action,
    ConditionalOrder,
    ConditionalStatus,
    OrderOrigin,
    Stake,
    TriggerCondition,
    as_utc,
)
from app.services.baskets import value_basket
from app.services.trading import TradingError, execute, needs_rebalance, plan_buy, plan_rebalance, plan_sell
from app.services import news, news_execution

log = logging.getLogger(__name__)


def triggered(condition: TriggerCondition, level: float, nav: float) -> bool:
    return nav <= level if condition == TriggerCondition.AT_OR_BELOW else nav >= level


def _usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _commit(session: Session) -> bool:
    try:
        session.commit()
        return True
    except StaleDataError:
        session.rollback()
        log.info("Account changed during an automated trade; retrying on the next sync")
        return False


def _fire(session: Session, order: ConditionalOrder, broker: Broker, now: datetime) -> bool:
    account, basket = order.account, order.basket
    try:
        if order.action == Action.BUY:
            plan = plan_buy(account, basket, order.amount, broker)
        else:
            plan = plan_sell(account, basket, order.amount, broker)
        order.basket_order = execute(session, account, basket, plan, broker, OrderOrigin.CONDITIONAL)
        order.status = ConditionalStatus.FILLED
    except TradingError as e:
        order.status, order.note = ConditionalStatus.FAILED, str(e)
    order.triggered_at = now
    return _commit(session) and order.status == ConditionalStatus.FILLED


def _recur(session: Session, stake: Stake, broker: Broker, now: datetime) -> bool:
    amount = stake.recurring_amount_cents
    placed = False
    try:
        plan = plan_buy(stake.account, stake.basket, amount / 100, broker)
        execute(session, stake.account, stake.basket, plan, broker, OrderOrigin.RECURRING)
        stake.automation_note = f"Recurring buy of {_usd(-plan.cash_delta_cents)} placed {now:%b %d}"
        placed = True
    except TradingError as e:
        stake.automation_note = f"Recurring buy skipped {now:%b %d}: {e}"
    # Missed runs (the API was down) aren't made up; the next one is the first still ahead.
    step = timedelta(days=stake.recurring_days or 1)
    due = as_utc(stake.next_recurring_at)
    while due <= now:
        due += step
    stake.next_recurring_at = due
    return _commit(session) and placed


def _rebalance(session: Session, stake: Stake, broker: Broker, now: datetime) -> bool:
    try:
        plan = plan_rebalance(stake.account, stake.basket, broker)
        execute(session, stake.account, stake.basket, plan, broker, OrderOrigin.AUTO_REBALANCE)
        stake.automation_note = f"Rebalanced automatically {now:%b %d}"
    except TradingError as e:
        stake.automation_note = f"Automatic rebalance skipped {now:%b %d}: {e}"
        _commit(session)
        return False
    return _commit(session)


def run(session: Session, brokers: dict[AccountMode, Broker], now: datetime) -> int:
    """Place every automated trade that's due. Returns how many basket orders were placed."""
    placed = 0
    for order in session.scalars(select(ConditionalOrder).where(ConditionalOrder.status == ConditionalStatus.OPEN)).all():
        # Locked arbitrage baskets are held to settlement; trading them by weight would break the lock.
        if news.blocks_automation(session, order.account_id, order.basket_id) or lock_for(session, order.basket_id):
            continue
        broker = brokers.get(order.account.mode)
        if broker and triggered(order.condition, order.nav, value_basket(order.basket).nav):
            placed += _fire(session, order, broker, now)

    stakes = session.scalars(
        select(Stake).where(or_(Stake.recurring_amount_cents.is_not(None), Stake.auto_rebalance_drift.is_not(None)))
    ).all()
    for stake in stakes:
        if news.blocks_automation(session, stake.account_id, stake.basket_id) or lock_for(session, stake.basket_id):
            continue
        broker = brokers.get(stake.account.mode)
        if broker is None:
            continue
        if stake.recurring_amount_cents and stake.next_recurring_at and as_utc(stake.next_recurring_at) <= now:
            placed += _recur(session, stake, broker, now)
        if stake.auto_rebalance_drift and needs_rebalance(stake.account, stake.basket, stake.auto_rebalance_drift):
            placed += _rebalance(session, stake, broker, now)
    placed += news_execution.run(session, brokers, now)
    return placed
