"""Owner-bound external execution API. Disabled until explicitly configured.

The synchronous /accounts/live trading routes stay unavailable: they cannot
provide durable asynchronous submission semantics.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError

from app.auth import CurrentUser
from app.kalshi.execution import VenueError
from app.db import SessionDep
from app.models import Account, AccountMode, Basket, Holding, Side
from app.news_models import ExchangeOrder, LiveControl, NewsCycle
from app.routers.common import owned_basket
from app.services import exchange_execution as execution, news, news_execution, trading

router = APIRouter(prefix="/exchange", tags=["exchange execution"])


def perform(action, *args):
    """Return actionable errors without exposing provider payloads or DB details."""
    try:
        return action(*args)
    except news.NewsError as error:
        raise HTTPException(409, str(error)) from error
    except VenueError as error:
        raise HTTPException(503, "Exchange evidence is unavailable or inconsistent; reconcile before continuing") from error
    except (IntegrityError, OperationalError, StaleDataError) as error:
        raise HTTPException(409, "Account state changed; refresh and check confirmed fills before continuing") from error


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_id: str = Field(min_length=1, max_length=64)


class ArmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basket_id: int


def gateway(request, user):
    g = request.app.state.exchange_gateway
    if g is None:
        raise HTTPException(503, "External execution is disabled; paper trading remains available")
    if g.owner_id != user.id:
        raise HTTPException(404, "No exchange account is bound to this user")
    return g


def context(request, session, user, basket_id):
    g = gateway(request, user)
    basket = owned_basket(session, user, basket_id)
    account = session.scalar(select(Account).where(Account.user_id == user.id, Account.mode == AccountMode.LIVE))
    control = session.get(LiveControl, account.id) if account else None
    if not control or control.basket_id != basket.id or control.scope != g.scope:
        raise HTTPException(409, "Connect this ETF to the configured exchange subaccount first")
    return g, account, basket, control


def output(session, account, basket):
    cycle = news.active_cycle(session, account.id, basket.id) or news.pending_review(session, account.id, basket.id)
    if cycle is None:
        cycle = session.scalar(select(NewsCycle).where(NewsCycle.account_id == account.id,
                              NewsCycle.basket_id == basket.id).order_by(NewsCycle.created_at.desc()).limit(1))
    orders = session.scalars(select(ExchangeOrder).where(ExchangeOrder.cycle_id == cycle.id)).all() if cycle else []
    try:
        proposal = news.proposal(session, account, basket)
    except news.NewsError as error:
        proposal = {"id": "blocked", "shifts": [], "reason": str(error)}
    return {"cycle": news.cycle_out(cycle), "orders": [{"client_id": o.id, "status": o.status,
              "phase": o.phase, "requested": o.request["count"], "confirmed": o.booked_count, "note": o.note} for o in orders],
            "basket_cash": news.basket_cash(session, account.id, basket.id) / 100,
            "proposal": proposal, "account_cash": account.cash,
            "cost_basis": "mark at connection; returns measured since connection"}


@router.get("/status")
def status(request: Request, user: CurrentUser):
    g = request.app.state.exchange_gateway
    return {"implemented": True, "enabled_for_user": g is not None and g.owner_id == user.id,
            "environment": g.venue.environment if g and g.owner_id == user.id else None,
            "real_orders_sent_by_this_check": 0}


@router.post("/connect")
def connect(body: ConnectIn, request: Request, session: SessionDep, user: CurrentUser):
    g = gateway(request, user)
    basket = owned_basket(session, user, body.basket_id)
    from app.arb_models import lock_for
    if lock_for(session, basket.id):
        raise HTTPException(409, "Locked arbitrage baskets are paper-only for now")
    if session.scalar(select(Account.id).where(Account.user_id == user.id, Account.mode == AccountMode.LIVE)):
        raise HTTPException(409, "An exchange account is already connected; its baseline cannot be reset")
    owner_id, basket_id = user.id, basket.id
    session.commit()
    snapshot = perform(g.venue.account_snapshot)  # Read-only; never places an order.
    basket = session.get(Basket, basket_id)
    allowed = {(p.market.external_id, p.side): p.market for p in basket.positions}
    imported = []
    for ticker, net in snapshot["positions"].items():
        side = Side.YES if net > 0 else Side.NO
        market = allowed.get((ticker, side))
        if market is None:
            raise HTTPException(409, "Exchange positions include markets or sides outside this ETF")
        imported.append((market, side, abs(net)))
    equity = snapshot["cash_cents"] + sum(round(m.price_for(s) * n * 100) for m, s, n in imported)
    account = Account(user_id=owner_id, mode=AccountMode.LIVE, cash_cents=snapshot["cash_cents"], starting_cash_cents=equity)
    session.add(account); session.flush()
    for market, side, count in imported:
        account.holdings.append(Holding(basket=basket, market=market, side=side, contracts=count,
                                       cost_cents=round(market.price_for(side) * count * 100)))
    session.add(LiveControl(account_id=account.id, basket_id=basket.id, armed=False, scope=g.scope))
    session.commit()
    return {"connected": True, "armed": False, "environment": g.venue.environment,
            "cost_basis": "mark at connection; historical acquisition profit is not inferred"}


@router.get("/baskets/{basket_id}")
def get(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    _, account, basket, control = context(request, session, user, basket_id)
    return {**output(session, account, basket), "armed": control.armed}


@router.post("/baskets/{basket_id}/apply")
def apply(basket_id: int, body: ApplyIn, request: Request, session: SessionDep, user: CurrentUser):
    g, account, basket, _ = context(request, session, user, basket_id)
    aid, bid = account.id, basket.id
    session.commit()
    cid = perform(execution.start_cycle, request.app.state.sessionmaker, g, aid, bid, body.expected_id)
    perform(execution.advance, request.app.state.sessionmaker, g, cid)
    session.expire_all()
    return output(session, session.get(Account, aid), session.get(Basket, bid))


@router.post("/baskets/{basket_id}/advance")
def advance(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    g, account, basket, _ = context(request, session, user, basket_id)
    cycle = news.active_cycle(session, account.id, basket.id) or news.pending_review(session, account.id, basket.id)
    if cycle is None:
        raise HTTPException(409, "No pending exchange cycle")
    aid, bid, cid = account.id, basket.id, cycle.id
    session.commit()
    perform(execution.advance, request.app.state.sessionmaker, g, cid)
    session.expire_all()
    return output(session, session.get(Account, aid), session.get(Basket, bid))


@router.put("/baskets/{basket_id}/automation")
def arm(basket_id: int, body: ArmIn, request: Request, session: SessionDep, user: CurrentUser):
    _, account, basket, control = context(request, session, user, basket_id)
    if body.enabled and news.pending_review(session, account.id, basket.id):
        raise HTTPException(409, "Review the unfinished exchange cycle first")
    control.armed = body.enabled
    if not body.enabled:
        cycle = news.active_cycle(session, account.id, basket.id)
        if cycle:
            cycle.payload = {**cycle.payload, "cancel_requested": True}
    session.commit()
    return {"armed": control.armed}


@router.post("/baskets/{basket_id}/cancel")
def cancel(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    g, account, basket, control = context(request, session, user, basket_id)
    control.armed = False
    cycle = news.active_cycle(session, account.id, basket.id)
    if cycle:
        cycle.payload = {**cycle.payload, "cancel_requested": True}
    session.commit()
    if cycle:
        perform(execution.advance, request.app.state.sessionmaker, g, cycle.id)
    session.expire_all()
    return output(session, account, basket)


@router.post("/baskets/{basket_id}/review")
def review(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    _, account, basket, control = context(request, session, user, basket_id)
    news_execution.lock_account(session, account)
    cycle = news.pending_review(session, account.id, basket.id)
    if not cycle:
        raise HTTPException(409, "No stopped cycle is ready for review")
    unresolved = session.scalar(select(ExchangeOrder.id).where(ExchangeOrder.cycle_id == cycle.id,
                       ExchangeOrder.status.in_(execution.PENDING)).limit(1))
    if unresolved:
        raise HTTPException(409, "Outstanding exchange orders/fills must reconcile before review")
    news.remember_allocation(session, account, basket)
    cycle.status, cycle.note = "REVIEWED", "Owner accepted current confirmed holdings and cash; external automation is off"
    control.armed = False
    session.commit()
    return output(session, account, basket)
