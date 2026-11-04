"""Arbitrage on Kalshi: groups of markets in one event whose prices break a rule of probability.

Two structures give a payout floor that holds whatever happens:

- Ladders. "Above $150k" can't resolve YES unless "above $120k" does. YES on the lower strike plus
  NO on the higher pays at least $1, so if the pair costs less, the gap is locked in.
- Brackets. At most one market in a mutually exclusive event resolves YES, so NO on k of them pays at
  least k - 1. When the event's ranges cover every outcome, exactly one resolves YES, so YES on all of
  them pays exactly 1.

A scan screens every group at the stored quotes from the last sync, prices the ones within a cent of
paying against the live order books (fees included), and logs the ones that still pay.
"""

import hashlib
import logging
import math
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.arb_models import ArbOpportunity, ArbScan, LockBasket, MarketTerms
from app.brokers import Book, Broker, NoQuote, Quote, gross_cents, top_of_book
from app.kalshi.base import MarketData
from app.models import (
    Account,
    Action,
    Basket,
    BasketNav,
    BasketOrder,
    BasketOrderKind,
    BasketPosition,
    Holding,
    Market,
    Order,
    OrderOrigin,
    Side,
    User,
    as_utc,
    utcnow,
)
from app.services import naming, trading
from app.services.baskets import BASE_NAV, value_basket
from app.services.trading import PlannedOrder, TradingError

log = logging.getLogger(__name__)

# Kalshi's taker fee, rate * price * (1 - price) per contract, for screening. Sizing uses the broker's own.
FEE_RATE = 0.07
# Groups this close to paying at the stored quotes (dollars per set) get priced against live books.
SCREEN_MARGIN = 0.01
# Most groups priced live per scan, and most kept per event.
MAX_CONFIRM = 60
MAX_PER_GROUP = 3
MAX_SETS = 100_000
LOCK_NAME_LENGTH = 80

KINDS = ("ladder", "bracket_no", "bracket_yes")
_UP = {"greater", "greater_or_equal"}
_DOWN = {"less", "less_or_equal"}


# ---- Terms: what the sync stores about each market ----------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def strike_template(rules: str, floor: float | None, cap: float | None) -> str | None:
    """Hash of the rules with the market's own strike blanked out ("above 3.75%" -> "above <strike>%").

    Equal ones mean the same measurement against different lines, which is what makes a ladder. A
    shared event alone doesn't: one event can hold "emissions under X by 2025" and "under Y by 2030".
    """
    strikes = [s for s in (floor, cap) if s is not None]
    if not rules or not strikes:
        return None

    def blank(m: re.Match) -> str:
        try:
            value = float(m.group().replace(",", ""))
        except ValueError:
            return m.group()
        return "<strike>" if any(abs(value - s) <= 1e-9 * max(1.0, abs(s)) for s in strikes) else m.group()

    text = " ".join(_NUMBER.sub(blank, rules.lower()).split())
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def terms_values(data: MarketData) -> dict:
    return {
        "strike_type": data.strike_type,
        "floor_strike": data.floor_strike,
        "cap_strike": data.cap_strike,
        "outcome": data.outcome,
        "event_title": data.event_title,
        "mutually_exclusive": data.mutually_exclusive,
        "event_size": data.event_size,
        "strike_template": strike_template(data.resolution_rules or "", data.floor_strike, data.cap_strike),
    }


def store_terms(session: Session, fetched: list[MarketData], by_ticker: dict[str, Market], now: datetime) -> None:
    """Upsert each fetched market's terms."""
    existing = {t.market_id: t for t in session.scalars(select(MarketTerms))}
    for data in fetched:
        market_id = by_ticker[data.external_id].id
        values = terms_values(data)
        row = existing.get(market_id)
        if row is None:
            session.add(MarketTerms(market_id=market_id, updated_at=now, **values))
        elif any(getattr(row, k) != v for k, v in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now


# ---- Screening at the stored quotes --------------------------------------------------------------


def _fee(price: float) -> float:
    return FEE_RATE * price * (1 - price)


@dataclass(frozen=True)
class _Row:
    market: Market
    terms: MarketTerms

    @property
    def label(self) -> str:
        return self.terms.outcome or self.market.question

    @property
    def event_title(self) -> str:
        return self.terms.event_title or self.market.question

    @property
    def threshold(self) -> float | None:
        """The one strike of a one-sided market ("above 3.75%", "below 100"); None for ranges and
        for "exactly N" markets Kalshi sometimes labels "less" with floor and cap both set."""
        t = self.terms
        if t.strike_type in _UP and t.floor_strike is not None and t.cap_strike is None:
            return t.floor_strike
        if t.strike_type in _DOWN and t.cap_strike is not None and t.floor_strike is None:
            return t.cap_strike
        return None

    def ask(self, side: Side) -> float | None:
        """The stored price to buy `side`, if it can be bought."""
        price = top_of_book(self.market, side, Action.BUY)
        return price if price is not None and 0 < price < 1 else None


@dataclass
class Candidate:
    kind: str
    # The event the legs come from, so one event can't fill the list.
    group: str
    legs: list[tuple[_Row, Side]]
    payout_per_set: int
    # Dollars per set at the stored quotes, after fees.
    edge: float
    title: str
    explanation: str


def _screen_edge(legs: list[tuple[_Row, Side]], payout_cents: int) -> float | None:
    total = 0.0
    for row, side in legs:
        price = row.ask(side)
        if price is None:
            return None
        total += price + _fee(price)
    return payout_cents / 100 - total


def _ladders(rows: list[_Row], counts: dict[str, int]) -> Iterator[Candidate]:
    """Pairs of strikes in one event where the narrower one costs more than the broader one allows."""
    ladders: dict[tuple, list[_Row]] = defaultdict(list)
    for r in rows:
        t = r.terms
        if r.market.event_ticker and not t.mutually_exclusive and t.strike_template and r.threshold is not None:
            ladders[(r.market.event_ticker, t.strike_type, t.strike_template)].append(r)
    for (event, strike_type, _), items in ladders.items():
        up = strike_type in _UP
        strikes = sorted(items, key=lambda r: r.threshold)
        for i, low in enumerate(strikes):
            for high in strikes[i + 1 :]:
                if high.threshold == low.threshold:
                    continue
                counts["groups"] += 1
                # Above the higher strike implies above the lower; below the lower implies below the higher.
                broad, narrow = (low, high) if up else (high, low)
                legs = [(broad, Side.YES), (narrow, Side.NO)]
                edge = _screen_edge(legs, 100)
                if edge is None or edge <= -SCREEN_MARGIN:
                    continue
                yield Candidate(
                    "ladder",
                    event,
                    legs,
                    100,
                    edge,
                    title=broad.event_title,
                    explanation=(
                        f"“{narrow.label}” can't resolve YES unless “{broad.label}” does. YES on “{broad.label}” "
                        f"plus NO on “{narrow.label}” pays at least $1 whatever happens."
                    ),
                )


@dataclass(frozen=True)
class _Span:
    lo: float
    lo_closed: bool
    hi: float
    hi_closed: bool


def _span(t: MarketTerms) -> _Span | None:
    """The values a range market resolves YES on. Floor and cap both set is a closed range, whatever the
    label ("between", or "less" on Kalshi's "exactly N" markets)."""
    if t.floor_strike is not None and t.cap_strike is not None:
        return _Span(t.floor_strike, True, t.cap_strike, True) if t.floor_strike <= t.cap_strike else None
    if t.strike_type in _DOWN and t.cap_strike is not None:
        return _Span(-math.inf, False, t.cap_strike, t.strike_type == "less_or_equal")
    if t.strike_type in _UP and t.floor_strike is not None:
        return _Span(t.floor_strike, t.strike_type == "greater_or_equal", math.inf, False)
    return None


def _covers_everything(items: list[_Row]) -> bool:
    """True when every one of the event's markets is here and their ranges leave no value uncovered."""
    size = items[0].terms.event_size
    if not size or len(items) != size:
        return False
    spans = [_span(r.terms) for r in items]
    if any(s is None for s in spans):
        return False
    spans.sort(key=lambda s: (s.lo, s.hi))
    if spans[0].lo != -math.inf or max(s.hi for s in spans) != math.inf:
        return False
    # Results come in whole units when every strike is whole, else in cents: nothing falls strictly
    # between "300 to 349" and "350 to 399", or between 67,999.99 and 68,000.
    finite = [x for s in spans for x in (s.lo, s.hi) if math.isfinite(x)]
    tick = 1.0 if all(float(x).is_integer() for x in finite) else 0.01
    reach, reach_closed = spans[0].hi, spans[0].hi_closed
    for s in spans[1:]:
        if s.lo > reach + 1e-9:
            # A gap: covered only if it's one tick between two closed ends.
            if not (reach_closed and s.lo_closed and s.lo - reach <= tick + 1e-9):
                return False
        elif abs(s.lo - reach) <= 1e-9 and not (reach_closed or s.lo_closed):
            # "Below 100" then "above 100": 100 itself wins neither.
            return False
        if s.hi > reach or (s.hi == reach and s.hi_closed):
            reach, reach_closed = s.hi, s.hi_closed
    return True


def _brackets(rows: list[_Row], counts: dict[str, int]) -> Iterator[Candidate]:
    events: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        if r.terms.mutually_exclusive and r.market.event_ticker:
            events[r.market.event_ticker].append(r)
    for event, items in events.items():
        if len(items) < 2:
            continue
        counts["groups"] += 1
        # NO on the k cheapest: at most one wins, so at least k - 1 pay. Keep the k with the best return.
        priced = sorted(((r, p) for r in items if (p := r.ask(Side.NO)) is not None), key=lambda x: x[1])
        best, cost = None, 0.0
        for k, (_, price) in enumerate(priced, start=1):
            cost += price + _fee(price)
            edge = (k - 1) - cost
            if k >= 2 and edge > -SCREEN_MARGIN and (best is None or edge / cost > best[0]):
                best = (edge / cost, k, edge)
        if best:
            _, k, edge = best
            yield Candidate(
                "bracket_no",
                event,
                [(r, Side.NO) for r, _ in priced[:k]],
                (k - 1) * 100,
                edge,
                title=items[0].event_title,
                explanation=f"At most one of these {k} outcomes can win, so NO on all {k} pays at least ${k - 1} whatever happens.",
            )
        if _covers_everything(items):
            counts["groups"] += 1
            legs = [(r, Side.YES) for r in items]
            edge = _screen_edge(legs, 100)
            if edge is not None and edge > -SCREEN_MARGIN:
                yield Candidate(
                    "bracket_yes",
                    event,
                    legs,
                    100,
                    edge,
                    title=items[0].event_title,
                    explanation=f"These {len(items)} ranges cover every possible result and exactly one wins, so YES on all of them pays exactly $1.",
                )


def screen(rows: list[_Row], counts: dict[str, int]) -> list[Candidate]:
    """Candidates close to paying at the stored quotes, best first, at most MAX_PER_GROUP per event."""
    found = sorted([*_ladders(rows, counts), *_brackets(rows, counts)], key=lambda c: c.edge, reverse=True)
    per_group: dict[tuple[str, str], int] = defaultdict(int)
    kept = []
    for c in found:
        key = (c.kind, c.group)
        if per_group[key] < MAX_PER_GROUP:
            per_group[key] += 1
            kept.append(c)
    return kept


# ---- Pricing against the order books --------------------------------------------------------------


def set_cost(books: list[Book], sets: int, broker: Broker) -> int | None:
    """Cents to buy `sets` contracts of every leg walking its book, fees included; None if a book is too thin."""
    total = 0
    for book in books:
        fills = book.take(sets)
        if round(sum(q for _, q in fills)) < sets:
            return None
        total += gross_cents(fills, Action.BUY) + broker.fee_cents(fills)
    return total


def max_sets(books: list[Book]) -> int:
    depth = min(b.depth() for b in books)
    return MAX_SETS if math.isinf(depth) else min(MAX_SETS, math.floor(depth + 1e-9))


def best_size(books: list[Book], payout_per_set: int, broker: Broker, limit: int | None = None) -> tuple[int, int, int] | None:
    """(sets, cost, profit) in cents for the size that makes the most money, or None if no size fills.

    Profit is linear between the depths where some book moves to its next price level, so the best
    size is one of those depths.
    """
    top = max_sets(books) if limit is None else min(max_sets(books), limit)
    if top < 1:
        return None
    sizes = {1, top}
    for book in books:
        depth = 0.0
        for _, size in book.levels:
            depth += size
            if math.isinf(depth):
                break
            if 1 <= (k := math.floor(depth + 1e-9)) <= top:
                sizes.add(k)
    best = None
    for k in sorted(sizes):
        cost = set_cost(books, k, broker)
        if cost is not None and (best is None or k * payout_per_set - cost > best[2]):
            best = (k, cost, k * payout_per_set - cost)
    return best


@dataclass
class Priced:
    candidate: Candidate
    books: list[Book]
    # Every book came from the exchange just now, so the size is backed by real depth.
    confirmed: bool
    sets: int
    cost_cents: int
    profit_cents: int


def price(candidates: list[Candidate], broker: Broker) -> list[Priced]:
    """The candidates that pay against the order books, sized. One books() call for every leg."""
    markets = {(r.market.id, side): r.market for c in candidates for r, side in c.legs}
    keys = list(markets)
    quotes: dict[tuple[int, Side], Quote] = dict(zip(keys, broker.books([(markets[k], k[1], Action.BUY) for k in keys])))
    out = []
    for c in candidates:
        books = [quotes[(r.market.id, side)] for r, side in c.legs]
        if any(isinstance(q, NoQuote) for q in books):
            continue
        live = all(b.live for b in books)
        # Stored quotes have no depth, so they're priced for one set only.
        sized = best_size(books, c.payout_per_set, broker, limit=None if live else 1)
        if sized is None or sized[2] <= 0:
            continue
        out.append(Priced(c, books, live, *sized))
    return out


# ---- Scanning and the opportunity log --------------------------------------------------------------


def opportunity_id(kind: str, legs: list[tuple[str, str]]) -> str:
    key = kind + "|" + ",".join(sorted(f"{ticker}:{side}" for ticker, side in legs))
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _leg_out(market: Market, side: Side, label: str, book: Book, sets: int) -> dict:
    fills = book.take(sets)
    count = sum(q for _, q in fills)
    return {
        "market_id": market.id,
        "ticker": market.external_id,
        "question": market.question,
        "outcome": label if label != market.question else None,
        "url": market.url,
        "side": side.value,
        "best_price": book.best,
        "avg_price": sum(p * q for p, q in fills) / count if count else None,
        "closes_at": as_utc(market.closes_at).isoformat() if market.closes_at else None,
    }


def _closes(markets: list[Market]) -> datetime | None:
    dates = [as_utc(m.closes_at) for m in markets if m.closes_at]
    return max(dates) if dates else None


def _upsert(session: Session, oid: str, now: datetime, **values) -> ArbOpportunity:
    row = session.get(ArbOpportunity, oid)
    if row is None:
        row = ArbOpportunity(id=oid, first_seen=now, times_seen=0, best_profit_cents=0)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    row.active, row.last_seen = True, now
    row.times_seen += 1
    row.best_profit_cents = max(row.best_profit_cents, values.get("profit_cents") or 0)
    return row


def scan(session: Session, broker: Broker, now: datetime | None = None) -> int:
    """Screen every group, price the promising ones live, and log what pays. Commits. Returns how many pay."""
    started, now = time.monotonic(), now or utcnow()
    rows = [
        _Row(m, t)
        for m, t in session.execute(
            select(Market, MarketTerms)
            .join(MarketTerms, MarketTerms.market_id == Market.id)
            .where(Market.is_open.is_(True), Market.settlement_price.is_(None))
        ).all()
    ]
    counts = {"groups": 0}
    candidates = screen(rows, counts)[:MAX_CONFIRM]
    found = price(candidates, broker) if candidates else []

    seen = set()
    for p in found:
        c = p.candidate
        oid = opportunity_id(c.kind, [(r.market.external_id, side.value) for r, side in c.legs])
        seen.add(oid)
        _upsert(
            session,
            oid,
            now,
            kind=c.kind,
            guaranteed=True,
            title=c.title,
            explanation=c.explanation,
            legs=[_leg_out(r.market, side, r.label, book, p.sets) for (r, side), book in zip(c.legs, p.books)],
            payout_per_set_cents=c.payout_per_set,
            edge_per_set=round(c.payout_per_set / 100 - sum(b.best + _fee(b.best) for b in p.books), 6),
            sets=p.sets if p.confirmed else None,
            cost_cents=p.cost_cents,
            payout_cents=p.sets * c.payout_per_set,
            profit_cents=p.profit_cents,
            closes_at=_closes([r.market for r, _ in c.legs]),
            confirmed=p.confirmed,
            note=None if p.confirmed else "No live order book: priced for one set at the last sync's quotes",
        )
    # Whatever a scan doesn't find any more has closed.
    session.execute(
        update(ArbOpportunity)
        .where(ArbOpportunity.active.is_(True), ArbOpportunity.id.not_in(seen))
        .values(active=False)
    )
    session.add(
        ArbScan(
            started_at=now,
            duration_ms=round((time.monotonic() - started) * 1000),
            markets=len(rows),
            groups=counts["groups"],
            candidates=len(candidates),
            found=len(found),
            profit_cents=sum(p.profit_cents for p in found),
        )
    )
    session.commit()
    return len(found)


def safe_scan(session: Session, broker: Broker | None, now: datetime | None = None) -> int:
    """Scan after a sync without ever failing it. Returns how many opportunities were found."""
    if broker is None:
        return 0
    try:
        return scan(session, broker, now)
    except Exception as e:
        session.rollback()
        log.warning("Arbitrage scan failed; trying again after the next sync", exc_info=True)
        session.add(ArbScan(started_at=now or utcnow(), error=str(e)[:500]))
        session.commit()
        return 0


def opportunities(session: Session) -> list[ArbOpportunity]:
    """Active opportunities, confirmed and most profitable first."""
    active = session.scalars(select(ArbOpportunity).where(ArbOpportunity.active.is_(True))).all()
    return sorted(active, key=lambda o: (o.confirmed, o.profit_cents or 0), reverse=True)


@dataclass(frozen=True)
class Summary:
    scans: int
    last_scan: ArbScan | None
    seen: int
    active: int
    profit_available_cents: int
    best_profit_seen_cents: int
    locks: int
    locked_cost_cents: int
    locked_payout_cents: int


def summary(session: Session, user: User, active: list[ArbOpportunity]) -> Summary:
    """What the scanner has seen, what's open now, and what this user has locked."""
    locks = user_locks(session, user)
    # Count locks still held or paid out; one sold before settlement no longer has its floor.
    ids = [lock.basket_id for lock in locks]
    live = set(session.scalars(select(Holding.basket_id).where(Holding.basket_id.in_(ids)))) | set(
        session.scalars(
            select(BasketOrder.basket_id).where(BasketOrder.basket_id.in_(ids), BasketOrder.kind == BasketOrderKind.SETTLE)
        )
    )
    locks = [lock for lock in locks if lock.basket_id in live]
    return Summary(
        scans=session.scalar(select(func.count()).select_from(ArbScan)) or 0,
        last_scan=session.scalar(select(ArbScan).order_by(ArbScan.started_at.desc(), ArbScan.id.desc()).limit(1)),
        seen=session.scalar(select(func.count()).select_from(ArbOpportunity)) or 0,
        active=len(active),
        profit_available_cents=sum(o.profit_cents or 0 for o in active if o.confirmed),
        best_profit_seen_cents=session.scalar(select(func.coalesce(func.sum(ArbOpportunity.best_profit_cents), 0))) or 0,
        locks=len(locks),
        locked_cost_cents=sum(lock.cost_cents for lock in locks),
        locked_payout_cents=sum(lock.payout_cents for lock in locks),
    )


def annualized(profit: float | None, cost: float | None, closes_at: datetime | None, now: datetime | None = None) -> float | None:
    """Simple (not compounded) yearly rate of the return, counting at least a day."""
    if not profit or not cost or cost <= 0 or closes_at is None:
        return None
    days = max((as_utc(closes_at) - (now or utcnow())).total_seconds() / 86_400, 1.0)
    return profit / cost * 365 / days


# ---- Locking an opportunity into a basket ---------------------------------------------------------


@dataclass
class LockPlan:
    opportunity: ArbOpportunity
    legs: list[tuple[Market, Side]]
    orders: list[PlannedOrder]
    sets: int
    cost_cents: int

    @property
    def payout_cents(self) -> int:
        return self.sets * self.opportunity.payout_per_set_cents

    @property
    def profit_cents(self) -> int:
        return self.payout_cents - self.cost_cents


def plan_lock(session: Session, account: Account, opp: ArbOpportunity, broker: Broker, sets: int | None = None) -> LockPlan:
    """Buy `sets` of every leg at the live books (default: the most profitable size), if it still pays."""
    legs = []
    for leg in opp.legs:
        market = session.get(Market, leg["market_id"])
        if market is None or not market.is_open:
            raise TradingError(f"“{leg['question']}” has closed, so this can't be locked any more")
        legs.append((market, Side(leg["side"])))
    quotes = broker.books([(m, side, Action.BUY) for m, side in legs])
    for (market, side), quote in zip(legs, quotes):
        if isinstance(quote, NoQuote):
            raise TradingError(f"Can't buy {side.value.upper()} on “{market.question}”: {quote.reason}")
        if not quote.live:
            raise TradingError("The live order book couldn't be read, so the lock can't be checked against real depth. Try again in a moment")
    books: list[Book] = quotes
    payout = opp.payout_per_set_cents
    if sets is None:
        sized = best_size(books, payout, broker)
        if sized is None or sized[2] <= 0:
            raise TradingError("This no longer pays at the current order books: the gap has closed")
        sets, cost, _ = sized
    else:
        cost = set_cost(books, sets, broker)
        if cost is None:
            raise TradingError(f"The order books can't fill {sets} sets: at most {max_sets(books)} are on offer")
        if sets * payout - cost <= 0:
            raise TradingError(f"At {sets} sets this doesn't pay: ${cost / 100:,.2f} for ${sets * payout / 100:,.2f} guaranteed")
    available = trading.spendable_cash(account)
    if cost > available:
        raise TradingError(f"Not enough cash: this costs ${cost / 100:,.2f} and ${available / 100:,.2f} is available")
    orders = [trading._planned(m, side, book, book.take(sets), broker) for (m, side), book in zip(legs, books)]
    return LockPlan(opp, legs, orders, sets, cost)


def _lock_basket(session: Session, user: User, plan: LockPlan, now: datetime) -> tuple[Basket, LockBasket]:
    """The user's basket for this opportunity, made on the first lock. Every leg holds the same units,
    so the basket's NAV is the value of one set."""
    opp = plan.opportunity
    lock = session.scalar(
        select(LockBasket)
        .join(Basket, Basket.id == LockBasket.basket_id)
        .where(LockBasket.opportunity_id == opp.id, Basket.owner_id == user.id)
    )
    if lock is not None:
        return session.get(Basket, lock.basket_id), lock
    prices = [max(m.price_for(side), 0.01) for m, side in plan.legs]
    units = BASE_NAV / sum(prices)
    taken = set(session.scalars(select(Basket.name).where(Basket.owner_id == user.id)))
    basket = Basket(
        owner_id=user.id,
        name=naming.unused_name(naming._truncate(f"Lock: {opp.title}", LOCK_NAME_LENGTH), taken),
        description=opp.explanation,
        positions=[
            BasketPosition(market=m, side=side, target_weight=p / sum(prices), units=units)
            for (m, side), p in zip(plan.legs, prices)
        ],
    )
    session.add(basket)
    session.flush()
    lock = LockBasket(
        basket_id=basket.id,
        opportunity_id=opp.id,
        kind=opp.kind,
        title=opp.title,
        explanation=opp.explanation,
        payout_per_set_cents=opp.payout_per_set_cents,
        closes_at=opp.closes_at,
        created_at=now,
    )
    session.add_all([lock, BasketNav(basket=basket, ts=now, nav=value_basket(basket).nav)])
    return basket, lock


@dataclass
class LockResult:
    basket: Basket
    lock: LockBasket
    record: BasketOrder
    sets: int


def execute_lock(session: Session, account: Account, user: User, plan: LockPlan, broker: Broker) -> LockResult:
    """Buy every leg in full, or raise so the caller rolls back and nothing is locked. Doesn't commit.

    Locks are paper-only, and paper fills are simulated, so a rollback undoes them.
    """
    from app.services.news_execution import _validate_fill, lock_account

    now = utcnow()
    lock_account(session, account)
    basket, lock = _lock_basket(session, user, plan, now)
    if not any(h.basket_id == basket.id for h in account.holdings):
        # Locking again after the last one was sold or paid out starts the totals over.
        lock.sets = lock.cost_cents = lock.payout_cents = 0
    orders = []
    for planned in plan.orders:
        request = planned.request
        fill = broker.execute(request)
        try:
            _validate_fill(fill, request)
        except ValueError as e:
            raise TradingError(str(e)) from e
        if fill.count < request.count:
            raise TradingError("The order book moved before every leg filled, so nothing was locked. Try again")
        orders.append(
            Order(
                market=request.market,
                side=request.side,
                action=request.action,
                count=request.count,
                limit_price=request.limit_price,
                filled_count=fill.count,
                fill_price=fill.price,
                gross_cents=fill.gross_cents,
                fee_cents=fill.fee_cents,
                realized_pnl_cents=None,
            )
        )

    record = BasketOrder(
        account_id=account.id,
        basket_id=basket.id,
        basket_name=basket.name,
        kind=BasketOrderKind.BUY,
        origin=OrderOrigin.ARBITRAGE,
        amount=plan.cost_cents / 100,
    )
    trading._settle_up(session, account, basket, record, orders, now)
    lock.sets += plan.sets
    lock.cost_cents += sum(o.gross_cents + o.fee_cents for o in orders)
    lock.payout_cents += plan.payout_cents
    return LockResult(basket, lock, record, plan.sets)


def lock_status(account: Account, basket_id: int, session: Session) -> tuple[str, int]:
    """("locked", sets still held) while holdings remain; else "settled" once a payout came in, or "closed"."""
    held = [h for h in account.holdings if h.basket_id == basket_id]
    if held:
        positions = session.get(Basket, basket_id).positions
        counts = {(h.market_id, h.side): h.contracts for h in held}
        return "locked", min(counts.get((p.market_id, p.side), 0) for p in positions)
    settled = session.scalar(
        select(BasketOrder.id)
        .where(BasketOrder.account_id == account.id, BasketOrder.basket_id == basket_id, BasketOrder.kind == BasketOrderKind.SETTLE)
        .limit(1)
    )
    return ("settled" if settled else "closed"), 0


def user_locks(session: Session, user: User) -> list[LockBasket]:
    return list(
        session.scalars(
            select(LockBasket)
            .join(Basket, Basket.id == LockBasket.basket_id)
            .where(Basket.owner_id == user.id)
            .order_by(LockBasket.created_at.desc())
        )
    )
