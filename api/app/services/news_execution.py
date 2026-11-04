"""Durable paper-only sell barrier and constituent-constrained purchase workflow.

Fills, account changes, reserved cash and progress commit together. A broker
with external side effects is deliberately rejected by this implementation.
"""
from copy import deepcopy
from datetime import timedelta
import math

from sqlalchemy import select, update
from sqlalchemy.orm.exc import StaleDataError

from app.brokers import Book, OrderRequest, PaperBroker
from app.models import Account, AccountMode, Action, BasketOrder, BasketOrderKind, Order, OrderOrigin, Side, as_utc, utcnow
from app.news_models import NewsCash, NewsConsumption, NewsCycle
from app.services import news, trading


def lock_account(session, account):
    """Take a database write lock before inspecting or changing fills and cash."""
    version = account.version
    result = session.execute(update(Account).where(Account.id == account.id, Account.version == version)
                             .values(version=version + 1), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        raise StaleDataError("Account changed; re-read before advancing news trades")
    session.refresh(account)


def _require_paper(account, broker):
    if account.mode != AccountMode.PAPER or not isinstance(broker, PaperBroker):
        raise news.NewsError("News execution is restricted to the local paper broker")


def _cash(session, account, basket):
    bucket = session.scalar(select(NewsCash).where(NewsCash.account_id == account.id, NewsCash.basket_id == basket.id))
    if bucket is None:
        bucket = NewsCash(account_id=account.id, basket_id=basket.id, cents=0)
        session.add(bucket)
    return bucket


def start(session, account, basket, broker, expected_id=None, now=None):
    _require_paper(account, broker)
    return _start_plan(session, account, basket, broker, expected_id, now)


def start_for_exchange(session, account, basket, quotes, scope, expected_id=None, now=None):
    if account.mode != AccountMode.LIVE:
        raise news.NewsError("External execution requires a bound live account")
    existing = news.active_cycle(session, account.id, basket.id)
    if existing and (existing.payload.get("execution_kind") != "exchange" or existing.payload.get("exchange_scope") != scope):
        raise news.NewsError("The active cycle is bound to a different exchange scope")
    cycle = _start_plan(session, account, basket, quotes, expected_id, now)
    cycle.payload = {**cycle.payload, "execution_kind": "exchange", "exchange_scope": scope}
    session.flush()
    return cycle


def _start_plan(session, account, basket, broker, expected_id=None, now=None):
    now = now or utcnow()
    lock_account(session, account)
    existing = news.active_cycle(session, account.id, basket.id)
    if existing:
        return existing
    if news.pending_review(session, account.id, basket.id):
        raise news.NewsError("Review the previous unfinished news cycle before starting another")
    config = news.policy(session, basket)
    if config.mode == "off":
        raise news.NewsError("News rebalancing is off")
    session.add(config)
    plan = news.proposal(session, account, basket, now)
    if expected_id and plan["id"] != expected_id:
        raise news.NewsError("The evidence or holdings changed; refresh the proposal")
    if plan.get("reason"):
        raise news.NewsError(plan["reason"])
    old = session.scalar(select(NewsCycle).where(NewsCycle.account_id == account.id,
                        NewsCycle.basket_id == basket.id, NewsCycle.event_key == plan["event_key"]))
    if old:
        return old
    markets = {p.market_id: p.market for p in basket.positions}
    sells, buys = [], []
    for shift in plan["shifts"]:
        delta = shift["proposed_weight"] - shift["current_weight"]
        count = math.floor(abs(delta) * plan["value"] / shift["mark"] + 1e-9) if shift["mark"] > 0 else 0
        if not count:
            continue
        action = Action.SELL if delta < 0 else Action.BUY
        quote = broker.books([(markets[shift["market_id"]], Side(shift["side"]), action)])[0]
        fills = quote.take(count) if isinstance(quote, Book) and quote.live else []
        # Missing quote cannot invent a price or imply infinite liquidity.
        limit = fills[-1][0] if fills else None
        leg = {"market_id": shift["market_id"], "side": shift["side"], "count": count,
               "filled": 0, "limit_price": limit, "weight_delta": abs(delta)}
        (sells if delta < 0 else buys).append(leg)
    if not sells:
        raise news.NewsError("The proposed reduction rounds down to zero whole contracts")
    # Never enlarge the intended sale merely because a book is thin.
    payload = {"constituents": news.constituents(basket),
               "execution_kind": "paper",
               "contract_hashes": {str(t["market_id"]): news.digest(t["contract"]) for t in news.watchlist(session, basket)["topics"]},
               "considered_signal_ids": sorted({sid for s in plan["shifts"] for sid in s["signal_ids"]}),
               "holdings": {f"{s['market_id']}:{s['side']}": s["contracts"] for s in plan["shifts"]},
               "sells": sells, "buys": buys, "proceeds_cents": 0, "spent_cents": 0,
               "signal_ids": plan["signal_ids"], "proposal_id": plan["id"]}
    sigs = [session.get(news.NewsSignal, sid) for sid in plan["signal_ids"]]
    specs = {t["market_id"]: t["contract"] for t in news.watchlist(session, basket)["topics"]}
    expires = min(min(news.signal_expires_at(s), news.utc(specs[s.market_id]["deadline"])) for s in sigs)
    cycle = NewsCycle(id=news.digest({"account": account.id, "basket": basket.id, "event": plan["event_key"]}),
                     account_id=account.id, basket_id=basket.id, event_key=plan["event_key"],
                     status="SELLING", payload=payload, expires_at=expires,
                     created_at=now, updated_at=now, note="Waiting for all planned sales to fill")
    session.add(cycle)
    for sid in plan["signal_ids"]:
        session.add(NewsConsumption(account_id=account.id, basket_id=basket.id, signal_id=sid))
    _cash(session, account, basket)
    session.flush()
    return cycle


def _validate_fill(fill, request):
    if not isinstance(fill.count, int) or not 0 <= fill.count <= request.count:
        raise news.NewsError("Invalid broker fill count")
    if not all(isinstance(v, int) and v >= 0 for v in (fill.gross_cents, fill.fee_cents)):
        raise news.NewsError("Invalid broker cash amounts")
    if not fill.count:
        if fill.gross_cents or fill.fee_cents:
            raise news.NewsError("An empty fill cannot change cash")
        return
    if fill.price is None or not math.isfinite(fill.price) or not 0 < fill.price < 1:
        raise news.NewsError("Invalid fill price")
    if abs(fill.gross_cents - fill.price * fill.count * 100) > 1.01:
        raise news.NewsError("Fill cash does not match its price and quantity")
    if request.action == Action.SELL:
        if fill.price < request.limit_price - 1e-9 or fill.fee_cents > fill.gross_cents:
            raise news.NewsError("Sale violates its price floor")
    elif fill.price > request.limit_price + 1e-9:
        raise news.NewsError("Purchase violates its price ceiling")


def advance(session, account, basket, broker, cycle, now=None):
    now = now or utcnow()
    _require_paper(account, broker)
    lock_account(session, account)
    session.refresh(cycle)
    if cycle.status not in news.ACTIVE:
        return cycle
    data = deepcopy(cycle.payload)
    cycle.updated_at = now
    if news.policy(session, basket).mode == "off":
        cycle.status, cycle.note = "CANCELLED", "News response turned off; proceeds remain basket cash"
        return cycle
    if as_utc(cycle.expires_at) <= now:
        cycle.status, cycle.note = "EXPIRED", "Evidence expired; unspent proceeds remain basket cash"
        return cycle
    if news.digest(news.constituents(basket)) != news.digest(data["constituents"]):
        cycle.status, cycle.note = "BLOCKED", "ETF constituents changed; review before continuing"
        return cycle
    contracts_now = {str(t["market_id"]): news.digest(t["contract"]) for t in news.watchlist(session, basket)["topics"]}
    if contracts_now != data["contract_hashes"]:
        cycle.status, cycle.note = "BLOCKED", "Contract rules or deadlines changed; fresh evidence is required"
        return cycle
    if cycle.status == "BUYING":
        recipients = {(leg["market_id"], leg["side"]) for leg in data["buys"]}
        for signal in news.available_signals(session, basket, now=now):
            if signal.id not in data["considered_signal_ids"] and (signal.market_id, signal.side) in recipients and signal.payload["result"].get("classification") == "ADVERSE":
                cycle.status, cycle.note = "BLOCKED", "New adverse evidence affects a planned recipient; proceeds remain cash"
                return cycle
    held = {(h.market_id, h.side.value): h for h in account.holdings if h.basket_id == basket.id}
    actual = {f"{mid}:{side}": h.contracts for (mid, side), h in held.items() if h.contracts > 0}
    expected = {k: v for k, v in data["holdings"].items() if v > 0}
    if actual != expected:
        cycle.status, cycle.note = "BLOCKED", "Holdings changed outside this cycle; review before continuing"
        return cycle
    bucket = _cash(session, account, basket)
    if account.cash_cents < news.reserved_cash(session, account.id):
        cycle.status, cycle.note = "BLOCKED", "Reserved basket cash no longer reconciles"
        return cycle
    allowed = {(p.market_id, p.side.value): p.market for p in basket.positions}
    records = []
    cash_before = bucket.cents
    # One call advances one phase. A partial sale never falls through to buying.
    selling = cycle.status == "SELLING"
    legs = data["sells"] if selling else data["buys"]
    for leg in legs:
        remaining = leg["count"] - leg["filled"]
        if remaining <= 0:
            continue
        key = (leg["market_id"], leg["side"])
        market = allowed.get(key)
        if market is None or not market.is_open or market.closes_at is None or as_utc(market.closes_at) <= now:
            cycle.status, cycle.note = "BLOCKED", "A planned ETF position cannot trade"
            break
        action = Action.SELL if selling else Action.BUY
        quote = broker.books([(market, Side(leg["side"]), action)])[0]
        if not isinstance(quote, Book) or not quote.live:
            cycle.note = "Waiting for a fresh order book with known liquidity"
            continue
        fills = quote.take(remaining, leg["limit_price"])
        if not fills:
            cycle.note = "Waiting for liquidity within the original price limit"
            continue
        if leg["limit_price"] is None:
            leg["limit_price"] = fills[-1][0]
        count = min(remaining, round(sum(q for _, q in fills)))
        if selling:
            if key not in held or count > held[key].contracts:
                raise news.NewsError("Sale exceeds the held ETF position")
        else:
            budget = min(bucket.cents, data["proceeds_cents"] - data["spent_cents"],
                         leg["budget_cents"] - leg.get("spent_cents", 0))
            capped = Book(Action.BUY, tuple((p, q) for p, q in quote.levels if p <= leg["limit_price"] + 1e-9), True)
            buy_leg = trading._BuyLeg(market, Side(leg["side"]), capped, budget)
            count = min(count, trading._affordable(broker, buy_leg, budget))
            if count <= 0:
                # Unspendable cents stay in the basket; never use the user's other cash.
                leg["count"] = leg["filled"]
                continue
        request = OrderRequest(market, Side(leg["side"]), action, count, leg["limit_price"])
        fill = broker.execute(request)
        _validate_fill(fill, request)
        if not selling and fill.gross_cents + fill.fee_cents > budget:
            raise news.NewsError("Purchase exceeds confirmed net sale proceeds")
        leg["filled"] += fill.count
        holding_key = f"{key[0]}:{key[1]}"
        data["holdings"][holding_key] += -fill.count if selling else fill.count
        if selling:
            net = fill.gross_cents - fill.fee_cents
            data["proceeds_cents"] += net
            bucket.cents += net
        else:
            paid = fill.gross_cents + fill.fee_cents
            data["spent_cents"] += paid
            leg["spent_cents"] = leg.get("spent_cents", 0) + paid
            bucket.cents -= paid
        records.append(Order(market=market, side=request.side, action=action, count=count,
                             limit_price=request.limit_price, filled_count=fill.count,
                             fill_price=fill.price, gross_cents=fill.gross_cents,
                             fee_cents=fill.fee_cents, realized_pnl_cents=None))
    if records:
        record = BasketOrder(account_id=account.id, basket_id=basket.id, basket_name=basket.name,
                             kind=BasketOrderKind.REBALANCE, origin=OrderOrigin.NEWS, amount=None)
        trading._settle_up(session, account, basket, record, records, now,
                           basket_cash_delta_cents=bucket.cents - cash_before)
    if cycle.status in news.ACTIVE and all(l["filled"] == l["count"] for l in legs):
        if selling and data["buys"]:
            # Each recipient has an independent cap; their budgets sum to no more than proceeds.
            denom = sum(l["weight_delta"] for l in data["buys"])
            for leg in data["buys"]:
                leg["budget_cents"] = math.floor(data["proceeds_cents"] * leg["weight_delta"] / denom)
            cycle.status, cycle.note = "BUYING", "All planned sales confirmed; purchases may now begin"
        else:
            cycle.status, cycle.note = "COMPLETE", "Finished; unallocated proceeds remain basket cash"
            news.remember_allocation(session, account, basket, now)
    cycle.payload = data
    session.flush()
    return cycle


def run(session, brokers, now=None):
    from app.models import Basket
    from app.news_models import NewsPolicy
    now = now or utcnow()
    advanced = 0
    accounts = session.scalars(select(Account).where(Account.mode == AccountMode.PAPER)).all()
    for account in accounts:
        broker = brokers.get(AccountMode.PAPER)
        if not isinstance(broker, PaperBroker):
            continue
        configs = session.scalars(select(NewsPolicy).where(NewsPolicy.mode != "off")).all()
        for config in configs:
            basket = session.get(Basket, config.basket_id)
            if not basket or basket.owner_id != account.user_id:
                continue
            from app.arb_models import lock_for
            if lock_for(session, basket.id):
                # Locked arbitrage is held to settlement; a news shift would trade its legs apart.
                continue
            try:
                cycle = news.active_cycle(session, account.id, basket.id)
                if cycle is None and config.mode == "auto":
                    cycle = start(session, account, basket, broker, now=now)
                    # Persist the immutable goal before any broker operation.
                    session.commit()
                if cycle:
                    advance(session, account, basket, broker, cycle, now)
                    advanced += 1
                session.commit()
            except news.NewsError as error:
                session.rollback()
                if cycle is not None:
                    failed = session.get(NewsCycle, cycle.id)
                    if failed and failed.status in news.ACTIVE:
                        failed.status, failed.note = "BLOCKED", str(error)
                        failed.updated_at = now
                        session.commit()
            except StaleDataError:
                session.rollback()
    return advanced
