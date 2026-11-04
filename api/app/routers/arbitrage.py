"""Arbitrage: what the scanner found, rescans, and locking."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.arb_models import ArbOpportunity, ArbScan, LockBasket
from app.auth import CurrentUser
from app.db import SessionDep
from app.models import AccountMode, as_utc, utcnow
from app.routers.accounts import _RACE, AccountDep, BrokerDep, _account_out
from app.routers.common import sync_fresh
from app.schemas import (
    ArbitrageOut,
    ArbLegOut,
    ArbSummaryOut,
    BasketOrderOut,
    LockIn,
    LockOut,
    LockPreviewOut,
    LockResultOut,
    OpportunityOut,
    PlannedOrderOut,
    ScanIn,
    ScanOut,
)
from app.services import arbitrage as svc
from app.services.trading import TradingError, spendable_cash

router = APIRouter(tags=["arbitrage"])


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


def opportunity_out(o: ArbOpportunity, now: datetime) -> OpportunityOut:
    cost, profit = _dollars(o.cost_cents), _dollars(o.profit_cents)
    return OpportunityOut(
        id=o.id,
        kind=o.kind,
        title=o.title,
        explanation=o.explanation,
        legs=[ArbLegOut(**leg) for leg in o.legs],
        payout_per_set=o.payout_per_set_cents / 100,
        edge_per_set=o.edge_per_set,
        sets=o.sets,
        cost=cost,
        payout=_dollars(o.payout_cents),
        profit=profit,
        return_pct=profit / cost if profit is not None and cost else None,
        annualized=svc.annualized(profit, cost, o.closes_at, now),
        closes_at=o.closes_at,
        confirmed=o.confirmed,
        active=o.active,
        first_seen=o.first_seen,
        last_seen=o.last_seen,
        times_seen=o.times_seen,
        best_profit=o.best_profit_cents / 100,
        note=o.note,
    )


def lock_out(lock: LockBasket, status: tuple[str, int] | None = None) -> LockOut:
    return LockOut(
        basket_id=lock.basket_id,
        opportunity_id=lock.opportunity_id,
        kind=lock.kind,
        title=lock.title,
        explanation=lock.explanation,
        payout_per_set=lock.payout_per_set_cents / 100,
        sets=lock.sets,
        cost=lock.cost_cents / 100,
        payout=lock.payout_cents / 100,
        profit=(lock.payout_cents - lock.cost_cents) / 100,
        closes_at=lock.closes_at,
        created_at=lock.created_at,
        status=status[0] if status else None,
        held_sets=status[1] if status else None,
    )


def _arbitrage_out(session, user) -> ArbitrageOut:
    now = utcnow()
    active = svc.opportunities(session)
    s = svc.summary(session, user, active)
    return ArbitrageOut(
        summary=ArbSummaryOut(
            scans=s.scans,
            last_scan=ScanOut.model_validate(s.last_scan) if s.last_scan else None,
            opportunities_seen=s.seen,
            active=s.active,
            profit_available=s.profit_available_cents / 100,
            best_profit_seen=s.best_profit_seen_cents / 100,
            locks=s.locks,
            locked_cost=s.locked_cost_cents / 100,
            locked_payout=s.locked_payout_cents / 100,
            locked_profit=(s.locked_payout_cents - s.locked_cost_cents) / 100,
        ),
        opportunities=[opportunity_out(o, now) for o in active],
    )


@router.get("/arbitrage", response_model=ArbitrageOut)
def get_arbitrage(session: SessionDep, user: CurrentUser):
    """What the latest scan found (one runs after every sync), and the benchmark so far."""
    return _arbitrage_out(session, user)


@router.post("/arbitrage/scan", response_model=ArbitrageOut)
def rescan(body: ScanIn, request: Request, session: SessionDep, user: CurrentUser):
    """Scan now: screen every ladder and bracket at the stored quotes, price the
    promising ones against the live order books, and log what pays. `sync` first pulls fresh
    markets from Kalshi (about half a minute), which scans as part of the sync."""
    state = request.app.state
    broker = state.brokers.get(AccountMode.PAPER)
    if broker is None:
        raise HTTPException(503, "No paper broker to price order books with")
    arrived = utcnow()
    if body.sync:
        sync_fresh(state)
    last = session.scalar(select(ArbScan.started_at).order_by(ArbScan.started_at.desc()).limit(1))
    # A sync that ran since this call arrived has scanned already.
    if last is None or as_utc(last) < arrived:
        with state.sync_lock:
            svc.scan(session, broker)
    return _arbitrage_out(session, user)


@router.get("/accounts/{mode}/arbitrage/locks", response_model=list[LockOut])
def list_locks(account: AccountDep, session: SessionDep, user: CurrentUser):
    """This user's lock baskets, newest first, with what the account still holds of each."""
    return [lock_out(lock, svc.lock_status(account, lock.basket_id, session)) for lock in svc.user_locks(session, user)]


@router.post("/accounts/{mode}/arbitrage/{opportunity_id}/lock", response_model=LockPreviewOut | LockResultOut)
def lock_opportunity(
    mode: AccountMode,
    opportunity_id: str,
    body: LockIn,
    account: AccountDep,
    broker: BrokerDep,
    session: SessionDep,
    user: CurrentUser,
):
    """Buy one contract of every leg per set into a lock basket that's held to settlement. Paper only.

    Re-prices against the live order books first and refuses (409) if it no longer pays. Without
    `sets`, buys the most profitable size the books allow. Every leg fills in full or nothing is
    locked. `dry_run` returns the priced orders without placing them. A second lock of the same
    opportunity adds to the same basket.
    """
    if mode != AccountMode.PAPER:
        raise HTTPException(409, "Locks are paper-only for now")
    opp = session.get(ArbOpportunity, opportunity_id)
    if opp is None:
        raise HTTPException(404, "Opportunity not found")
    try:
        plan = svc.plan_lock(session, account, opp, broker, body.sets)
    except TradingError as e:
        raise HTTPException(409, str(e)) from e

    if body.dry_run:
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
        return LockPreviewOut(
            opportunity_id=opp.id,
            sets=plan.sets,
            orders=orders,
            cost=plan.cost_cents / 100,
            payout=plan.payout_cents / 100,
            profit=plan.profit_cents / 100,
            fees=sum(o.fee for o in orders),
            return_pct=plan.profit_cents / plan.cost_cents,
            annualized=svc.annualized(plan.profit_cents, plan.cost_cents, opp.closes_at),
            cash_after=(spendable_cash(account) - plan.cost_cents) / 100,
        )

    try:
        result = svc.execute_lock(session, account, user, plan, broker)
        session.commit()
    except TradingError as e:
        session.rollback()
        raise HTTPException(409, str(e)) from e
    except StaleDataError as e:
        session.rollback()
        raise HTTPException(409, _RACE) from e
    return LockResultOut(
        lock=lock_out(result.lock, svc.lock_status(account, result.basket.id, session)),
        basket_order=BasketOrderOut.model_validate(result.record),
        filled_sets=result.sets,
        account=_account_out(account, broker),
    )
