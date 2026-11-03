"""Durable external-order workflow. No network request runs inside a DB transaction.

PREPARED -> SUBMITTING is committed once. Uncertain submissions are reconciled
by client ID, never submitted again. Individual fill IDs are booked once under
the existing account version lock. All purchases wait for the original sale goals.
"""
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from datetime import timedelta
import math
import hashlib
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from app.brokers import Book, NoQuote, PaperBroker
from app.kalshi.execution import Intent, OrderRejected, SubmissionUnknown, VenueError
from app.models import Account, AccountMode, Action, Basket, BasketOrder, BasketOrderKind, Order, OrderOrigin, Side, as_utc, utcnow
from app.news_models import ExchangeFill, ExchangeOrder, NewsCycle
from app.services import news, news_execution, trading

PENDING = ("PREPARED", "SUBMITTING", "SUBMITTED", "UNKNOWN", "CANCEL_PENDING", "BLOCKED")


@dataclass
class Gateway:
    venue: object
    quotes: object
    owner_id: int
    scope: str
    max_order_cents: int
    max_cycle_cents: int
    max_fee_per_contract_cents: int
    max_daily_turnover_cents: int

    def validate(self, account, cycle=None):
        if account.mode != AccountMode.LIVE or account.user_id != self.owner_id:
            raise news.NewsError("Exchange account binding mismatch")
        if min(self.max_order_cents, self.max_cycle_cents, self.max_daily_turnover_cents) <= 0 or self.max_fee_per_contract_cents < 0:
            raise news.NewsError("Explicit exchange execution limits are required")
        if cycle and (cycle.payload.get("execution_kind") != "exchange" or cycle.payload.get("exchange_scope") != self.scope):
            raise news.NewsError("Saved exchange scope does not match the configured account")


def intent_for(order):
    r = order.request
    return Intent(order.id, r["ticker"], Side(r["side"]), Action(r["action"]), r["count"],
                  Decimal(r["limit_price"]), r["subaccount"])


def expected_positions(account):
    positions, sides = {}, {}
    for h in account.holdings:
        if h.contracts <= 0:
            continue
        ticker = h.market.external_id
        if ticker in sides and sides[ticker] != h.side:
            raise news.NewsError("Opposite-side allocations cannot share a netted exchange account")
        sides[ticker] = h.side
        positions[ticker] = positions.get(ticker, 0) + h.contracts * (1 if h.side == Side.YES else -1)
    return positions


def _stop_reason(session, account, basket, cycle, now):
    d = cycle.payload
    if d.get("cancel_requested"):
        return "CANCELLED", "Owner cancelled the cycle; reconciling all external orders"
    if news.policy(session, basket).mode == "off":
        return "CANCELLED", "News response is off; reconciling outstanding orders before stopping"
    if as_utc(cycle.expires_at) <= now:
        return "EXPIRED", "Evidence expired; unspent proceeds remain ETF cash"
    if news.digest(news.constituents(basket)) != news.digest(d["constituents"]):
        return "BLOCKED", "ETF constituents changed"
    specs = {str(t["market_id"]): news.digest(t["contract"]) for t in news.watchlist(session, basket)["topics"]}
    if specs != d["contract_hashes"]:
        return "BLOCKED", "Contract rules or deadlines changed"
    held = {f"{h.market_id}:{h.side.value}": h.contracts for h in account.holdings if h.basket_id == basket.id and h.contracts > 0}
    if held != {k: v for k, v in d["holdings"].items() if v > 0}:
        return "BLOCKED", "Holdings changed outside the saved cycle"
    if cycle.status == "BUYING":
        recipients = {(l["market_id"], l["side"]) for l in d["buys"]}
        for s in news.available_signals(session, basket, now=now):
            if s.id not in d["considered_signal_ids"] and (s.market_id, s.side) in recipients and s.payload["result"].get("classification") == "ADVERSE":
                return "BLOCKED", "New adverse evidence affects a recipient"
    return None


def start(session, gateway, account, basket, expected_id=None, now=None):
    gateway.validate(account)
    other = session.scalar(select(NewsCycle.id).where(NewsCycle.account_id == account.id,
        NewsCycle.basket_id != basket.id, NewsCycle.status.in_(news.ACTIVE)))
    if other:
        raise news.NewsError("Another ETF cycle is using this exchange account")
    cycle = news_execution.start_for_exchange(session, account, basket, gateway.quotes, gateway.scope, expected_id, now)
    gross_bound = sum(l["count"] * 100 for l in cycle.payload["sells"] + cycle.payload["buys"])
    if gross_bound > gateway.max_cycle_cents:
        raise news.NewsError("Cycle exceeds the configured turnover limit")
    return cycle


def start_cycle(factory, gateway, account_id, basket_id, expected_id=None, now=None):
    with factory() as session:
        account, basket = session.get(Account, account_id), session.get(Basket, basket_id)
        gateway.validate(account)
        markets = [(p.market, p.side, a) for p in basket.positions for a in (Action.SELL, Action.BUY)]
    quotes = gateway.quotes.books(markets)
    saved = {(m.id, s, a): q for (m, s, a), q in zip(markets, quotes)}
    class FrozenBooks:
        def books(self, legs):
            return [saved.get((m.id, s, a), NoQuote("No planned quote")) for m, s, a in legs]
    with factory() as session:
        cycle = start(session, replace(gateway, quotes=FrozenBooks()), session.get(Account, account_id),
                      session.get(Basket, basket_id), expected_id, now)
        session.commit()
        return cycle.id


def dispatch(factory, gateway, order_id):
    """Commit the send claim before contacting Kalshi. Exactly one POST per intent."""
    with factory() as session:
        order = session.get(ExchangeOrder, order_id)
        if not order or order.scope != gateway.scope:
            raise news.NewsError("Order scope mismatch")
        cycle = session.get(NewsCycle, order.cycle_id)
        account = session.get(Account, order.account_id)
        news_execution.lock_account(session, account)
        session.refresh(cycle); session.refresh(order)
        gateway.validate(account, cycle)
        if cycle.account_id != account.id or order.scope != gateway.scope:
            raise news.NewsError("Committed order account scope mismatch")
        reason = _stop_reason(session, account, session.get(Basket, cycle.basket_id), cycle, utcnow())
        if cycle.status not in news.ACTIVE or reason:
            session.execute(update(ExchangeOrder).where(ExchangeOrder.id == order.id, ExchangeOrder.status == "PREPARED")
                            .values(status="ABANDONED", note="Plan stopped before submission"))
            session.commit()
            return
        request = intent_for(order)
        if request.count * 100 > gateway.max_order_cents:
            order.status, order.note = "REJECTED", "Committed intent exceeds the current order limit"
            session.commit()
            return
        claimed = session.execute(update(ExchangeOrder).where(ExchangeOrder.id == order.id, ExchangeOrder.status == "PREPARED")
                                  .values(status="SUBMITTING", updated_at=utcnow()))
        session.commit()
        if claimed.rowcount != 1:
            return
    try:
        venue_id = gateway.venue.submit(request)
        state, note = "SUBMITTED", None
    except OrderRejected as error:
        venue_id, state, note = None, "REJECTED", str(error)
    except (VenueError, OSError, TimeoutError):
        venue_id, state, note = None, "UNKNOWN", "Submission outcome unknown; only reconciliation is permitted"
    with factory() as session:
        order = session.get(ExchangeOrder, order_id)
        # Another worker may already have recovered and reconciled the order by client ID.
        if order.status == "SUBMITTING":
            order.venue_order_id, order.status, order.note = venue_id, state, note
            order.updated_at = utcnow()
            session.commit()


def reconcile(factory, gateway, order_id):
    with factory() as session:
        order = session.get(ExchangeOrder, order_id)
        gateway.validate(session.get(Account, order.account_id), session.get(NewsCycle, order.cycle_id))
        if order.status in {"PREPARED", "REJECTED", "ABANDONED"}:
            return
        request, venue_id = intent_for(order), order.venue_order_id
    try:
        snapshot = gateway.venue.reconcile(request, venue_id)
    except SubmissionUnknown:
        with factory() as session:
            order = session.get(ExchangeOrder, order_id)
            if order.status not in {"RECONCILED", "REJECTED", "ABANDONED"}:
                order.status, order.note = "UNKNOWN", "Order/fills have not reconciled; no replacement order will be sent"
                session.commit()
        return
    except (VenueError, OSError, TimeoutError):
        with factory() as session:
            order = session.get(ExchangeOrder, order_id)
            order.status, order.note = "BLOCKED", "Exchange evidence is inconsistent; accounting review required"
            session.commit()
        return
    with factory() as session:
        order = session.get(ExchangeOrder, order_id)
        account, cycle = session.get(Account, order.account_id), session.get(NewsCycle, order.cycle_id)
        news_execution.lock_account(session, account)
        session.refresh(order); session.refresh(cycle)
        gateway.validate(account, cycle)
        if snapshot.filled_count < order.booked_count or snapshot.filled_count > request.count:
            raise news.NewsError("Exchange fill count regressed or exceeded the intent")
        basket = session.get(Basket, cycle.basket_id)
        data = deepcopy(cycle.payload)
        legs = data["sells"] if order.phase == "SELL" else data["buys"]
        leg = legs[order.leg_index]
        market = next((p.market for p in basket.positions if p.market_id == leg["market_id"] and p.side.value == leg["side"]), None)
        new, records = [], []
        for fill in snapshot.fills:
            body = {"count": fill.count, "price": str(fill.price), "gross_cents": fill.gross_cents, "fee_cents": fill.fee_cents}
            fingerprint = news.digest(body)
            old = session.scalar(select(ExchangeFill).where(ExchangeFill.scope == gateway.scope, ExchangeFill.venue_fill_id == fill.fill_id))
            if old:
                if old.order_id != order.id or old.fingerprint != fingerprint:
                    raise news.NewsError("A confirmed fill identity changed")
                continue
            new.append((fill, fingerprint))
        if sum(f.count for f, _ in new) + order.booked_count != snapshot.filled_count:
            raise news.NewsError("Confirmed fill ledger does not match the exchange")
        # Existing fills must still be booked when a market closes or the policy changes.
        if market is None:
            from app.models import Market
            market = session.get(Market, leg["market_id"])
        bucket = news_execution._cash(session, account, basket)
        cash_before = bucket.cents
        order.venue_order_id, order.venue_status = snapshot.order_id, snapshot.status
        order.last_evidence = {"filled_count": snapshot.filled_count, "remaining_count": snapshot.remaining_count,
                               "fills": [{"id": f.fill_id, "count": f.count, "price": str(f.price),
                                          "gross_cents": f.gross_cents, "fee_cents": f.fee_cents} for f in snapshot.fills]}
        quantity = sum(f.count for f, _ in new)
        total_paid = sum(f.gross_cents + f.fee_cents for f, _ in new)
        current_held = {f"{h.market_id}:{h.side.value}": h.contracts for h in account.holdings if h.basket_id == basket.id and h.contracts > 0}
        expected_held = {k: v for k, v in data["holdings"].items() if v > 0}
        error = None
        if current_held != expected_held or leg["filled"] + quantity > leg["count"]:
            error = "Confirmed execution conflicts with tracked holdings; accounting review required"
        elif order.phase == "BUY" and (total_paid > bucket.cents or total_paid + data["spent_cents"] > data["proceeds_cents"] or total_paid + leg.get("spent_cents", 0) > leg["budget_cents"]):
            error = "Confirmed execution exceeds reserved cash; accounting review required"
        elif order.phase == "SELL" and any(f.fee_cents > f.gross_cents for f, _ in new):
            error = "Confirmed sale fee exceeds proceeds; accounting review required"
        if error:
            order.status, order.note = "BLOCKED", error
            session.commit()
            return
        for fill, fingerprint in new:
            paid = fill.gross_cents + fill.fee_cents
            if order.phase == "SELL":
                net = fill.gross_cents - fill.fee_cents
                if net < 0:
                    raise news.NewsError("Sale fee exceeds gross proceeds")
                bucket.cents += net
                data["proceeds_cents"] += net
            else:
                bucket.cents -= paid
                data["spent_cents"] += paid
                leg["spent_cents"] = leg.get("spent_cents", 0) + paid
            leg["filled"] += fill.count
            if leg["filled"] > leg["count"]:
                raise news.NewsError("Confirmed fills exceed the saved ETF goal")
            key = f"{leg['market_id']}:{leg['side']}"
            data["holdings"][key] += -fill.count if order.phase == "SELL" else fill.count
            session.add(ExchangeFill(order_id=order.id, scope=gateway.scope, venue_fill_id=fill.fill_id,
                                    fingerprint=fingerprint, count=fill.count, gross_cents=fill.gross_cents, fee_cents=fill.fee_cents))
            records.append(Order(market=market, side=Side(leg["side"]), action=Action.SELL if order.phase == "SELL" else Action.BUY,
                                 count=fill.count, limit_price=float(request.limit_price), filled_count=fill.count,
                                 fill_price=float(fill.price), gross_cents=fill.gross_cents, fee_cents=fill.fee_cents,
                                 realized_pnl_cents=None))
        if records:
            record = BasketOrder(account_id=account.id, basket_id=basket.id, basket_name=basket.name,
                                 kind=BasketOrderKind.REBALANCE, origin=OrderOrigin.NEWS, amount=None)
            trading._settle_up(session, account, basket, record, records, utcnow(),
                               basket_cash_delta_cents=bucket.cents - cash_before)
        order.booked_count = snapshot.filled_count
        order.venue_order_id, order.venue_status = snapshot.order_id, snapshot.status
        order.status = "RECONCILED" if snapshot.terminal else ("CANCEL_PENDING" if order.cancel_attempted else "SUBMITTED")
        order.updated_at = cycle.updated_at = utcnow()
        cycle.payload = data
        session.commit()


def _cancel(factory, gateway, order_id):
    with factory() as session:
        order = session.get(ExchangeOrder, order_id)
        if order.status == "PREPARED":
            session.execute(update(ExchangeOrder).where(ExchangeOrder.id == order.id, ExchangeOrder.status == "PREPARED")
                            .values(status="ABANDONED"))
            session.commit()
            return
        if order.status in {"RECONCILED", "REJECTED", "ABANDONED"} or not order.venue_order_id or order.cancel_attempted:
            return
        claimed = session.execute(update(ExchangeOrder).where(ExchangeOrder.id == order.id,
                          ExchangeOrder.cancel_attempted.is_(False), ExchangeOrder.status.in_(PENDING))
                          .values(cancel_attempted=True, status="CANCEL_PENDING"))
        if claimed.rowcount != 1:
            session.rollback(); return
        order.status = "CANCEL_PENDING"
        venue_id = order.venue_order_id
        session.commit()
    try:
        gateway.venue.cancel(venue_id)
    except (VenueError, OSError, TimeoutError):
        return  # Cancellation acknowledgement is not proof that no later fill occurred.


def advance(factory, gateway, cycle_id, now=None):
    now = now or utcnow()
    # Reconcile previously sent orders even after expiry, cancellation, or a process restart.
    with factory() as session:
        cycle = session.get(NewsCycle, cycle_id)
        if not cycle:
            raise news.NewsError("Unknown cycle")
        gateway.validate(session.get(Account, cycle.account_id), cycle)
        ids = list(session.scalars(select(ExchangeOrder.id).where(ExchangeOrder.cycle_id == cycle.id)))
    for oid in ids:
        reconcile(factory, gateway, oid)
    with factory() as session:
        cycle = session.get(NewsCycle, cycle_id)
        account, basket = session.get(Account, cycle.account_id), session.get(Basket, cycle.basket_id)
        news_execution.lock_account(session, account)
        session.refresh(cycle)
        gateway.validate(account, cycle)
        if cycle.status not in news.ACTIVE:
            session.rollback()
            return
        orders = session.scalars(select(ExchangeOrder).where(ExchangeOrder.cycle_id == cycle.id)).all()
        reason = _stop_reason(session, account, basket, cycle, now)
        bad = next((o for o in orders if o.status in {"REJECTED", "BLOCKED"}), None)
        if bad:
            reason = ("BLOCKED", bad.note or "Exchange order requires review")
        pending = [o for o in orders if o.status in PENDING]
        if reason:
            cycle.note = reason[1]
            if not pending:
                cycle.status = reason[0]
            cancel_ids = [o.id for o in pending]
            session.commit()
            for oid in cancel_ids:
                _cancel(factory, gateway, oid)
            return
        if pending:
            prepared = next((o.id for o in pending if o.status == "PREPARED"), None)
            session.commit()
            if prepared:
                dispatch(factory, gateway, prepared)
            return
        data = deepcopy(cycle.payload)
        selling = cycle.status == "SELLING"
        legs = data["sells"] if selling else data["buys"]
        if all(l["filled"] == l["count"] for l in legs):
            if selling and data["buys"]:
                denom = sum(l["weight_delta"] for l in data["buys"])
                for leg in data["buys"]:
                    leg["budget_cents"] = math.floor(data["proceeds_cents"] * leg["weight_delta"] / denom)
                cycle.status, cycle.note = "BUYING", "All original sales confirmed; purchases may begin"
            else:
                cycle.status, cycle.note = "COMPLETE", "Confirmed exchange fills reconciled; allocation saved"
                news.remember_allocation(session, account, basket, now)
            cycle.payload = data
            session.commit()
            return
        leg_index = next(i for i, l in enumerate(legs) if l["filled"] < l["count"])
        leg = legs[leg_index]
        market = next(p.market for p in basket.positions if p.market_id == leg["market_id"] and p.side.value == leg["side"])
        if not market.is_open or not market.closes_at or as_utc(market.closes_at) <= now:
            cycle.status, cycle.note = "BLOCKED", "Planned market cannot trade"
            session.commit(); return
        expected_cash, expected_net, version = account.cash_cents, expected_positions(account), account.version
        session.commit()
    # The venue snapshot must match the complete tracked account before another side effect.
    external = gateway.venue.account_snapshot()
    if external["cash_cents"] != expected_cash or external["positions"] != expected_net:
        with factory() as session:
            cycle = session.get(NewsCycle, cycle_id)
            cycle.status, cycle.note = "BLOCKED", "Exchange cash/positions differ from the app; reconcile before trading"
            session.commit()
        return
    action = Action.SELL if selling else Action.BUY
    quote = gateway.quotes.books([(market, Side(leg["side"]), action)])[0]
    if not isinstance(quote, Book) or not quote.live:
        return
    remaining = leg["count"] - leg["filled"]
    fills = quote.take(remaining, leg["limit_price"])
    if not fills:
        return
    limit = leg["limit_price"] if leg["limit_price"] is not None else fills[-1][0]
    count = min(remaining, round(sum(q for _, q in fills)), gateway.max_order_cents // 100)
    if not selling:
        budget = min(data["proceeds_cents"] - data["spent_cents"], leg["budget_cents"] - leg.get("spent_cents", 0))
        per_contract = math.ceil(limit * 100) + gateway.max_fee_per_contract_cents
        count = min(count, budget // per_contract)
    with factory() as session:
        account, cycle = session.get(Account, account.id), session.get(NewsCycle, cycle_id)
        if account.version != version:
            return
        news_execution.lock_account(session, account)
        session.refresh(cycle)
        if _stop_reason(session, account, session.get(Basket, cycle.basket_id), cycle, now):
            session.rollback(); return
        current = deepcopy(cycle.payload)
        target = (current["sells"] if selling else current["buys"])[leg_index]
        if target["filled"] != leg["filled"] or cycle.status != ("SELLING" if selling else "BUYING"):
            session.rollback(); return
        if count <= 0:
            if not selling:
                target["count"] = target["filled"]
                cycle.payload = current
                session.commit()
            return
        daily = sum(o.request["count"] * 100 for o in session.scalars(select(ExchangeOrder).where(
            ExchangeOrder.scope == gateway.scope, ExchangeOrder.created_at >= now - timedelta(days=1))).all())
        if daily + count * 100 > gateway.max_daily_turnover_cents:
            cycle.status, cycle.note = "BLOCKED", "Configured daily turnover limit reached"
            session.commit(); return
        phase = "SELL" if selling else "BUY"
        attempt = len(session.scalars(select(ExchangeOrder.id).where(ExchangeOrder.cycle_id == cycle.id,
                           ExchangeOrder.phase == phase, ExchangeOrder.leg_index == leg_index)).all())
        oid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{gateway.scope}:{cycle.id}:{phase}:{leg_index}:{attempt}"))
        target["limit_price"] = limit
        cycle.payload = current
        session.add(ExchangeOrder(id=oid, cycle_id=cycle.id, account_id=account.id, scope=gateway.scope,
                                  phase=phase, leg_index=leg_index, attempt=attempt, status="PREPARED", booked_count=0,
                                  request={"ticker": market.external_id, "side": leg["side"], "action": action.value,
                                           "count": count, "limit_price": str(limit), "subaccount": gateway.venue.subaccount}))
        session.commit()
    dispatch(factory, gateway, oid)


def make_gateway(settings, source):
    """Registration only; construction performs no venue request."""
    if not settings.live_enabled:
        return None
    if settings.live_environment == "production" and not settings.live_production_enabled:
        raise news.NewsError("Production execution needs a separate explicit activation")
    required = [settings.live_owner_id, settings.live_key_id, settings.live_private_key_path,
                settings.live_max_order_cents, settings.live_max_cycle_cents, settings.live_max_daily_turnover_cents]
    if any(v is None or v == "" for v in required) or settings.live_max_fee_per_contract_cents is None:
        raise news.NewsError("Live execution needs owner binding, exchange credentials and explicit limits")
    if not settings.live_experimental_model_accepted:
        raise news.NewsError("The trained model's experimental status must be explicitly acknowledged before activation")
    from pathlib import Path
    from app.kalshi.execution import KalshiExecution, Signer
    venue = KalshiExecution(Signer(settings.live_key_id, Path(settings.live_private_key_path).read_bytes()),
                            environment=settings.live_environment, subaccount=settings.live_subaccount)
    credential_scope = hashlib.sha256(settings.live_key_id.encode()).hexdigest()[:24]
    return Gateway(venue, PaperBroker(settings.paper_fee_rate, source), settings.live_owner_id,
                   f"kalshi:{settings.live_environment}:owner:{settings.live_owner_id}:subaccount:{settings.live_subaccount}:key:{credential_scope}",
                   settings.live_max_order_cents, settings.live_max_cycle_cents, settings.live_max_fee_per_contract_cents,
                   settings.live_max_daily_turnover_cents)


def run(factory, gateway):
    if gateway is None:
        return 0
    from app.news_models import LiveControl
    with factory() as session:
        controls = session.scalars(select(LiveControl)).all()
        targets = [(c.account_id, c.basket_id, c.armed, c.scope) for c in controls]
    count = 0
    for account_id, basket_id, armed, scope in targets:
        if scope != gateway.scope:
            continue
        with factory() as session:
            cycle = news.active_cycle(session, account_id, basket_id)
            cycle_id = cycle.id if cycle else None
            review = news.pending_review(session, account_id, basket_id) is not None
        try:
            if cycle_id is None and armed and not review:
                cycle_id = start_cycle(factory, gateway, account_id, basket_id)
            if cycle_id:
                advance(factory, gateway, cycle_id)
                count += 1
        except (StaleDataError, OperationalError):
            # A competing account lock won. Committed send claims remain durable.
            continue
        except (news.NewsError, VenueError):
            # No write is retried. Existing outbox state remains available for reconciliation.
            with factory() as session:
                c = news.active_cycle(session, account_id, basket_id)
                if c:
                    c.note = "Exchange workflow needs reconciliation or review; no replacement submission is permitted"
                    session.commit()
    return count
