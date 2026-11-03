from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class MarketData:
    """A Kalshi market, normalized to what we store.

    Kalshi quotes prices in dollars per $1 contract; yes_price here is that same
    0..1 number, read as a probability.
    """

    external_id: str
    question: str
    yes_price: float
    volume: float = 0.0
    category: str | None = None
    url: str | None = None
    closes_at: datetime | None = None
    is_open: bool = True
    event_ticker: str | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    resolution_rules: str | None = None
    resolution_deadline: datetime | None = None


@dataclass(frozen=True)
class MarketQuote:
    """A market's current price, without the listing details only a full sync reads."""

    yes_price: float
    yes_bid: float | None = None
    yes_ask: float | None = None
    volume: float = 0.0


# Ticker -> [(timestamp, yes_price)], oldest first.
PriceHistory = dict[str, list[tuple[datetime, float]]]

# (price, contracts) resting at that price. Kalshi's contract counts can be fractional.
Level = tuple[float, float]


@dataclass(frozen=True)
class OrderBook:
    """A market's resting bids, best (highest) first.

    Kalshi's book has only bids: a NO bid at p is a YES ask at 1 - p, and the reverse.
    """

    yes_bids: tuple[Level, ...]
    no_bids: tuple[Level, ...]


class MarketSource(Protocol):
    """Where markets come from: the live Kalshi API, or a fake in tests."""

    def fetch_markets(self) -> list[MarketData]: ...

    def fetch_history(self, tickers: list[str], days: int) -> PriceHistory: ...

    def fetch_orderbooks(self, tickers: list[str]) -> dict[str, OrderBook]:
        """Current books. A ticker the source doesn't know is left out."""
        ...

    def fetch_quotes(self, tickers: list[str]) -> dict[str, MarketQuote]:
        """Current prices of the tickers whose markets are still trading. Others are left out."""
        ...

    def fetch_results(self, tickers: list[str]) -> dict[str, float]:
        """What a YES contract paid, for the tickers whose markets have resolved. Others are left out."""
        ...
