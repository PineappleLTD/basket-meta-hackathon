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


# Ticker -> [(timestamp, yes_price)], oldest first.
PriceHistory = dict[str, list[tuple[datetime, float]]]


class MarketSource(Protocol):
    """Where markets come from: the live Kalshi API, or a fake in tests."""

    def fetch_markets(self) -> list[MarketData]: ...

    def fetch_history(self, tickers: list[str], days: int) -> PriceHistory: ...
