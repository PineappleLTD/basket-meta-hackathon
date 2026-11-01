"""Where orders go: simulated fills for paper accounts, an exchange for live ones.

Contracts are whole numbers and pay $1 if their side wins, so a price is dollars
per contract. On Kalshi's book a NO bid is a YES ask at 1 - price, so the NO
quotes come from the YES ones. Money that changes hands is whole cents: what a
buy pays rounds up, what a sell receives rounds down.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.kalshi import Level, OrderBook
from app.models import Action, Market, Side, as_utc, utcnow

log = logging.getLogger(__name__)

# Float slack when comparing prices and contract counts.
_EPS = 1e-9


def to_cents(dollars: float, up: bool) -> int:
    # Rounded first so float error like 7.0000000001 doesn't add a cent.
    cents = round(dollars * 100, 6)
    return math.ceil(cents) if up else math.floor(cents)


def kalshi_fee_cents(fills: list[Level], rate: float) -> int:
    """Kalshi's trading fee on an order's fills: rate * contracts * price * (1 - price), rounded up to the cent."""
    return to_cents(sum(rate * q * p * (1 - p) for p, q in fills), up=True)


def gross_cents(fills: list[Level], action: Action) -> int:
    """What the fills pay (a buy) or receive (a sell), before fees."""
    return to_cents(sum(p * q for p, q in fills), up=action == Action.BUY)


@dataclass(frozen=True)
class Book:
    """Liquidity one side and action can trade against, best price first.

    `live` books come from the exchange just now. Otherwise it's the stored top of book from
    the last sync, whose depth isn't known, so its one level holds unlimited contracts.
    """

    action: Action
    levels: tuple[Level, ...]
    live: bool

    @property
    def best(self) -> float:
        return self.levels[0][0]

    def within(self, price: float, limit: float) -> bool:
        return price <= limit + _EPS if self.action == Action.BUY else price >= limit - _EPS

    def depth(self, limit: float | None = None) -> float:
        return sum(q for p, q in self.levels if limit is None or self.within(p, limit))

    def take(self, count: int, limit: float | None = None) -> list[Level]:
        """Fills for up to `count` whole contracts, best price first, never past `limit`."""
        depth = self.depth(limit)
        left = count if math.isinf(depth) else min(count, math.floor(depth + _EPS))
        fills = []
        for price, size in self.levels:
            if left <= _EPS or (limit is not None and not self.within(price, limit)):
                break
            filled = min(size, left)
            fills.append((price, filled))
            left -= filled
        return fills


@dataclass(frozen=True)
class NoQuote:
    reason: str


Quote = Book | NoQuote


@dataclass(frozen=True)
class OrderRequest:
    market: Market
    side: Side
    action: Action
    count: int
    # Worst price the order may fill at: the deepest level it was sized against. A live
    # broker sends it as the limit.
    limit_price: float


@dataclass(frozen=True)
class Fill:
    count: int
    # Average price per contract; None when nothing filled.
    price: float | None
    gross_cents: int
    fee_cents: int


NO_FILL = Fill(count=0, price=None, gross_cents=0, fee_cents=0)


class Broker(Protocol):
    def books(self, legs: list[tuple[Market, Side, Action]]) -> list[Quote]:
        """What each leg can trade against now, or why it can't trade."""
        ...

    def fee_cents(self, fills: list[Level]) -> int: ...

    def execute(self, order: OrderRequest) -> Fill: ...


class BookSource(Protocol):
    def fetch_orderbooks(self, tickers: list[str]) -> dict[str, OrderBook]: ...


def top_of_book(market: Market, side: Side, action: Action) -> float | None:
    """Best price on the market's stored book for buying (the ask) or selling (the bid) `side`."""
    if side == Side.YES:
        return market.yes_ask if action == Action.BUY else market.yes_bid
    yes = market.yes_bid if action == Action.BUY else market.yes_ask
    return None if yes is None else 1.0 - yes


def _consumes_no_bids(side: Side, action: Action) -> bool:
    """Buying YES lifts NO bids and selling NO hits them; the other two trade against YES bids."""
    return (side == Side.YES) == (action == Action.BUY)


def _tradeable(price: float) -> bool:
    return _EPS < price < 1 - _EPS


def _age(minutes: float) -> str:
    return f"{minutes / 60:.0f} hours" if minutes >= 120 else f"{minutes:.0f} minutes"


class PaperBroker:
    """Fills orders against the exchange's live book, as a taker, without sending them.

    Each order walks the book from the best price, so size moves the price, and an order
    takes only what rests within its limit: if the book thinned since it was planned, it
    fills partly. Books are fetched for every trade and reused for `cache_seconds`, so a
    preview and the order placed right after it see the same prices; fills use up the
    cached liquidity. When the live book can't be fetched, the stored top of book from the
    last sync stands in, with unknown depth, as long as it's younger than `max_quote_age`.
    Closed markets don't trade.
    """

    def __init__(
        self,
        fee_rate: float,
        source: BookSource | None = None,
        max_quote_age: timedelta = timedelta(minutes=30),
        cache_seconds: float = 15.0,
    ):
        self.fee_rate = fee_rate
        self.source = source
        self.max_quote_age = max_quote_age
        self.cache_seconds = cache_seconds
        # ticker -> (fetched at, [yes bids, no bids]), bids as mutable [price, size] best first.
        self._cache: dict[str, tuple[float, list[list[list[float]]]]] = {}
        # Reentrant: execute holds it while books() fetches.
        self._lock = threading.RLock()

    def fee_cents(self, fills: list[Level]) -> int:
        return kalshi_fee_cents(fills, self.fee_rate)

    def books(self, legs: list[tuple[Market, Side, Action]]) -> list[Quote]:
        live = self._live({m.external_id for m, _, _ in legs if m.is_open})
        return [self._quote(m, side, action, live.get(m.external_id)) for m, side, action in legs]

    def execute(self, order: OrderRequest) -> Fill:
        with self._lock:
            quote = self.books([(order.market, order.side, order.action)])[0]
            if isinstance(quote, NoQuote) or order.count <= 0:
                return NO_FILL
            fills = quote.take(order.count, order.limit_price)
            count = round(sum(q for _, q in fills))
            if count == 0:
                return NO_FILL
            if quote.live:
                self._consume(order, fills)
        gross = gross_cents(fills, order.action)
        return Fill(count=count, price=sum(p * q for p, q in fills) / count, gross_cents=gross, fee_cents=self.fee_cents(fills))

    def _live(self, tickers: set[str]) -> dict[str, list[list[list[float]]]]:
        if self.source is None or not tickers:
            return {}
        now = time.monotonic()
        with self._lock:
            fresh = {t: c[1] for t, c in self._cache.items() if t in tickers and now - c[0] < self.cache_seconds}
            missing = sorted(tickers - fresh.keys())
            if missing:
                try:
                    fetched = self.source.fetch_orderbooks(missing)
                except Exception:
                    log.warning("Couldn't fetch order books; using stored quotes", exc_info=True)
                    fetched = {}
                for ticker, book in fetched.items():
                    sides = [[list(level) for level in book.yes_bids], [list(level) for level in book.no_bids]]
                    self._cache[ticker] = (now, sides)
                    fresh[ticker] = sides
            return fresh

    def _quote(self, market: Market, side: Side, action: Action, live: list[list[list[float]]] | None) -> Quote:
        if not market.is_open:
            return NoQuote("market has resolved" if market.is_settled else "market is closed")
        missing = NoQuote("no asks" if action == Action.BUY else "no bids")
        if live is not None:
            bids = live[1] if _consumes_no_bids(side, action) else live[0]
            # A buy pays the opposite side's bid flipped: 1 - p.
            # Rounded so float error from the flip doesn't show up as 0.18000000000000005.
            prices = ((round(1 - p, 6) if action == Action.BUY else p, q) for p, q in bids)
            levels = tuple((p, q) for p, q in prices if q > _EPS and _tradeable(p))
            return Book(action, levels, live=True) if levels else missing

        price = top_of_book(market, side, action)
        if price is None or not _tradeable(price):
            return missing
        age = utcnow() - as_utc(market.quoted_at) if market.quoted_at else None
        if age is None or age > self.max_quote_age:
            stale = f", and the last one is {_age(age.total_seconds() / 60)} old" if age else ""
            return NoQuote(f"no live quote{stale}")
        return Book(action, ((price, math.inf),), live=False)

    def _consume(self, order: OrderRequest, fills: list[Level]) -> None:
        cached = self._cache.get(order.market.external_id)
        if cached is None:
            return
        bids = cached[1][1 if _consumes_no_bids(order.side, order.action) else 0]
        for price, size in fills:
            bid = 1 - price if order.action == Action.BUY else price
            for level in bids:
                if abs(level[0] - bid) < _EPS:
                    level[1] -= size
        bids[:] = [level for level in bids if level[1] > _EPS]
