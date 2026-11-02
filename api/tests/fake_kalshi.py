"""Offline stand-in for the Kalshi API, so tests run without a network."""

import math
import random
from datetime import UTC, datetime, timedelta

from app.kalshi.base import MarketData, MarketQuote, OrderBook, PriceHistory


# Shared shocks that move groups of markets together. A market's loading scales its
# exposure to its factor; a negative loading moves it against the factor.
_FACTORS = ("rates", "crypto", "politics", "tech")

# (ticker, question, category, yes_price, volume, closes_at, factor, loading)
_FIXTURES = [
    ("MOCK-FEDCUT-26DEC", "Will the Fed cut rates at its December 2026 meeting?", "economics", 0.62, 4_200_000, "2026-12-10", "rates", 1.0),
    ("MOCK-CPI-26NOV-B3", "Will US CPI YoY be under 3% for November 2026?", "economics", 0.71, 850_000, "2026-12-15", "rates", 0.8),
    ("MOCK-RECSS-27", "US recession declared in 2027?", "economics", 0.27, 1_100_000, "2027-12-31", "rates", 0.6),
    ("MOCK-BTC-26DEC31-150K", "Will Bitcoin reach $150k by end of 2026?", "crypto", 0.18, 6_500_000, "2026-12-31", "crypto", 1.0),
    ("MOCK-ETHSTAKE-27", "Will a US spot ETH ETF offer staking by 2027?", "crypto", 0.55, 720_000, "2027-01-01", "crypto", 0.8),
    ("MOCK-HOUSE-26-D", "Democrats take the House in 2026?", "politics", 0.67, 12_300_000, "2026-11-03", "politics", 1.0),
    ("MOCK-SENATE-26-R", "Will Republicans keep the Senate in 2026?", "politics", 0.73, 7_400_000, "2026-11-03", "politics", -0.9),
    ("MOCK-AGI-30", "Will a lab announce AGI before 2030?", "tech", 0.14, 95_000, "2030-01-01", "tech", 1.0),
    ("MOCK-OAIFRONTIER-27", "Will OpenAI release a new frontier model before 2027?", "tech", 0.81, 2_300_000, "2027-01-01", "tech", 0.7),
    ("MOCK-SPACEXMARS-27", "Will SpaceX launch an uncrewed Mars mission by 2027?", "science", 0.09, 60_000, "2027-12-31", None, 0.0),
    ("MOCK-HURCAT5-26", "Will a Category 5 hurricane make US landfall in 2026?", "climate", 0.12, 310_000, "2026-11-30", None, 0.0),
]

# Log-odds move per step for a unit shock.
VOLATILITY = 0.08
# Size of each market's own noise relative to a unit factor shock.
IDIOSYNCRATIC = 0.5
# Quotes sit this far either side of the price.
HALF_SPREAD = 0.005
# Each side of the book rests BOOK_DEPTH contracts at each of BOOK_LEVELS prices, a cent apart.
BOOK_LEVELS = 5
BOOK_DEPTH = 500.0


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _expit(z: float) -> float:
    return 1 / (1 + math.exp(-z))


class FakeKalshi:
    """Offline stand-in for the Kalshi API.

    Prices follow a random walk in log-odds space, so they stay inside (0, 1).
    Markets that share a factor move together, which gives the price history
    real correlation structure. The first fetch returns the starting prices;
    each later fetch advances the walk one step.

    Order books rest BOOK_DEPTH contracts at each of BOOK_LEVELS cents from the
    quotes outward; set `books[ticker]` to replace one. `resolve` takes a market
    off the listing with a result, as Kalshi does once it closes and settles.
    """

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._prices = {f[0]: f[3] for f in _FIXTURES}
        self._fetched = False
        self.books: dict[str, OrderBook] = {}
        self.results: dict[str, float] = {}
        # Set to an exception to make order book fetches fail with it.
        self.books_error: Exception | None = None

    def resolve(self, ticker: str, payout: float) -> None:
        self.results[ticker] = payout

    def fetch_orderbooks(self, tickers: list[str]) -> dict[str, OrderBook]:
        if self.books_error:
            raise self.books_error
        books = {}
        for t in tickers:
            if t in self.books:
                books[t] = self.books[t]
            elif t in self._prices and t not in self.results:
                bid, ask = self._quotes(t)
                books[t] = OrderBook(
                    yes_bids=tuple((round(bid - i / 100, 4), BOOK_DEPTH) for i in range(BOOK_LEVELS)),
                    no_bids=tuple((round(1 - ask - i / 100, 4), BOOK_DEPTH) for i in range(BOOK_LEVELS)),
                )
        return books

    def fetch_quotes(self, tickers: list[str]) -> dict[str, MarketQuote]:
        """Current prices, without advancing the walk."""
        volumes = {f[0]: float(f[4]) for f in _FIXTURES}
        return {
            t: MarketQuote(self._prices[t], *self._quotes(t), volumes[t])
            for t in tickers
            if t in self._prices and t not in self.results
        }

    def fetch_results(self, tickers: list[str]) -> dict[str, float]:
        return {t: self.results[t] for t in tickers if t in self.results}

    def _quotes(self, ticker: str) -> tuple[float, float]:
        price = self._prices[ticker]
        return round(price - HALF_SPREAD, 4), round(price + HALF_SPREAD, 4)

    def fetch_markets(self) -> list[MarketData]:
        if self._fetched:
            self._prices = self._step(self._prices)
        self._fetched = True
        return [
            MarketData(
                external_id=external_id,
                question=question,
                category=category,
                yes_price=self._prices[external_id],
                yes_bid=self._quotes(external_id)[0],
                yes_ask=self._quotes(external_id)[1],
                volume=float(volume),
                closes_at=datetime.fromisoformat(closes).replace(tzinfo=UTC),
            )
            for external_id, question, category, _, volume, closes, _, _ in _FIXTURES
            if external_id not in self.results
        ]

    def fetch_history(self, tickers: list[str], days: int) -> PriceHistory:
        """`days` of daily prices ending the day before now."""
        now = datetime.now(UTC)
        path = self.backfill(days)
        return {
            t: [(now - timedelta(days=days - i), snapshot[t]) for i, snapshot in enumerate(path)]
            for t in tickers
            if t in self._prices
        }

    def backfill(self, steps: int) -> list[dict[str, float]]:
        """`steps` earlier price snapshots leading up to the current prices, oldest first.

        The walk is symmetric, so walking backward from now is a valid history.
        """
        path, prices = [], self._prices
        for _ in range(steps):
            prices = self._step(prices)
            path.append(prices)
        return path[::-1]

    def _step(self, prices: dict[str, float]) -> dict[str, float]:
        shocks = {f: self._rng.gauss(0, 1) for f in _FACTORS}
        stepped = {}
        for external_id, *_, factor, loading in _FIXTURES:
            move = self._rng.gauss(0, IDIOSYNCRATIC)
            if factor:
                move += loading * shocks[factor]
            z = _logit(prices[external_id]) + VOLATILITY * move
            stepped[external_id] = round(min(max(_expit(z), 0.01), 0.99), 4)
        return stepped
