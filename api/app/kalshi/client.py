"""Client for Kalshi's public market-data API. Reads need no account or key."""

import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import httpx

from app.kalshi.base import Level, MarketData, MarketQuote, OrderBook, PriceHistory

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
_PAGE_SIZE = 200
# The batch candlesticks endpoint rejects more tickers than this per request.
_CANDLES_BATCH = 100
# Tickers per orderbook or market lookup. These go in the query string, and 150 long
# tickers overflow Kalshi's URL limit.
_LOOKUP_BATCH = 50
# Statuses of a market whose result is known.
_RESOLVED = {"determined", "finalized", "settled"}
_DAY_MINUTES = 1440
_MAX_RETRIES = 5
# Kalshi's basic tier allows 20 reads/s, but heavy requests (100-ticker candlestick batches)
# drew 429s at 10/s over a full first sync; 4/s ran clean.
DEFAULT_REQUESTS_PER_SECOND = 4.0
# A 429 doubles the gap between requests up to this; each success eases it back.
_MAX_INTERVAL = 2.0
_RECOVERY = 0.9


def _dollars(value: str | None) -> float | None:
    return float(value) if value else None


def _yes_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """Mid of a two-sided quote, else the last trade."""
    if bid is not None and ask is not None and 0 < bid <= ask < 1:
        return round((bid + ask) / 2, 4)
    return last


def _levels(rows: list[list[str]] | None) -> tuple[Level, ...]:
    """Kalshi lists a side's bids lowest first; return them best first, empty levels dropped."""
    levels = ((float(p), float(q)) for p, q in rows or [])
    return tuple(sorted(((p, q) for p, q in levels if q > 0), reverse=True))


def _payout(market: dict) -> float | None:
    """What a YES contract paid, or None while the market is unresolved."""
    if market.get("status") not in _RESOLVED or not market.get("result"):
        return None
    value = _dollars(market.get("settlement_value_dollars"))
    if value is not None:
        return value
    return {"yes": 1.0, "no": 0.0}.get(market["result"])


def _fallback_title(event: dict, market: dict) -> str:
    """A few markets have no title; name them by event plus outcome instead."""
    outcome = market.get("yes_sub_title")
    return f"{event['title']}: {outcome}" if outcome else event["title"]


class KalshiClient:
    """Live Kalshi markets, filtered to ones worth building baskets from.

    Keeps active markets with at least `min_volume` contracts traded, and skips
    whole event categories (e.g. Sports, which is mostly games that close within days).
    Requests are spaced to at most `requests_per_second`. Kalshi's limit also counts over
    longer windows, so a 429 slows the pace further until requests succeed again.
    """

    def __init__(
        self,
        min_volume: float,
        exclude_categories: Iterable[str],
        http: httpx.Client | None = None,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    ):
        self.min_volume = min_volume
        self.exclude_categories = set(exclude_categories)
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=30)
        self._base_interval = 1 / requests_per_second if requests_per_second > 0 else 0.0
        self._interval = self._base_interval
        self._last_request = 0.0

    def fetch_markets(self) -> list[MarketData]:
        params = {"status": "open", "with_nested_markets": "true"}
        # Cursor pages over live data can repeat an event if the listing shifts mid-scan.
        markets: dict[str, MarketData] = {}
        for event in self._paginate("/events", "events", params):
            category = event.get("category")
            if category in self.exclude_categories:
                continue
            for m in event.get("markets", []):
                if m["status"] != "active" or float(m["volume_fp"]) < self.min_volume:
                    continue
                bid, ask = _dollars(m.get("yes_bid_dollars")), _dollars(m.get("yes_ask_dollars"))
                price = _yes_price(bid, ask, _dollars(m.get("last_price_dollars")))
                if price is None:
                    continue
                close_time = m.get("close_time")
                markets[m["ticker"]] = MarketData(
                    external_id=m["ticker"],
                    question=m.get("title") or _fallback_title(event, m),
                    yes_price=price,
                    volume=float(m["volume_fp"]),
                    category=category,
                    url=f"https://kalshi.com/markets/{event['series_ticker'].lower()}",
                    closes_at=datetime.fromisoformat(close_time) if close_time else None,
                    event_ticker=m["event_ticker"],
                    yes_bid=bid,
                    yes_ask=ask,
                )
        return list(markets.values())

    def fetch_history(self, tickers: list[str], days: int) -> PriceHistory:
        """Daily prices from candlesticks. Days without a quote or trade are skipped."""
        end = int(time.time())
        params = {"start_ts": end - days * 86_400, "end_ts": end, "period_interval": _DAY_MINUTES}
        history: PriceHistory = {}
        for i in range(0, len(tickers), _CANDLES_BATCH):
            batch = ",".join(tickers[i : i + _CANDLES_BATCH])
            data = self._get("/markets/candlesticks", {**params, "market_tickers": batch})
            for entry in data["markets"]:
                points = []
                for candle in entry["candlesticks"]:
                    # The current day's candle is still open; its close isn't final.
                    if candle["end_period_ts"] > end:
                        continue
                    price = _yes_price(
                        _dollars(candle.get("yes_bid", {}).get("close_dollars")),
                        _dollars(candle.get("yes_ask", {}).get("close_dollars")),
                        _dollars(candle.get("price", {}).get("close_dollars")),
                    )
                    if price is not None:
                        points.append((datetime.fromtimestamp(candle["end_period_ts"], UTC), price))
                history[entry["market_ticker"]] = points
        return history

    def fetch_orderbooks(self, tickers: list[str]) -> dict[str, OrderBook]:
        books = {}
        for i in range(0, len(tickers), _LOOKUP_BATCH):
            params = [("tickers", t) for t in tickers[i : i + _LOOKUP_BATCH]]
            for entry in self._get("/markets/orderbooks", params)["orderbooks"]:
                book = entry.get("orderbook_fp") or {}
                books[entry["ticker"]] = OrderBook(_levels(book.get("yes_dollars")), _levels(book.get("no_dollars")))
        return books

    def fetch_quotes(self, tickers: list[str]) -> dict[str, MarketQuote]:
        quotes = {}
        for i in range(0, len(tickers), _LOOKUP_BATCH):
            batch = tickers[i : i + _LOOKUP_BATCH]
            data = self._get("/markets", {"tickers": ",".join(batch), "limit": len(batch)})
            for m in data["markets"]:
                if m["status"] != "active":
                    continue
                bid, ask = _dollars(m.get("yes_bid_dollars")), _dollars(m.get("yes_ask_dollars"))
                price = _yes_price(bid, ask, _dollars(m.get("last_price_dollars")))
                if price is not None:
                    quotes[m["ticker"]] = MarketQuote(price, bid, ask, float(m.get("volume_fp") or 0))
        return quotes

    def fetch_results(self, tickers: list[str]) -> dict[str, float]:
        results = {}
        for i in range(0, len(tickers), _LOOKUP_BATCH):
            batch = tickers[i : i + _LOOKUP_BATCH]
            data = self._get("/markets", {"tickers": ",".join(batch), "limit": len(batch)})
            for m in data["markets"]:
                payout = _payout(m)
                if payout is not None:
                    results[m["ticker"]] = payout
        return results

    def _paginate(self, path: str, key: str, params: dict) -> Iterator[dict]:
        cursor = None
        while True:
            data = self._get(path, {**params, "limit": _PAGE_SIZE, **({"cursor": cursor} if cursor else {})})
            yield from data[key]
            cursor = data.get("cursor")
            if not cursor or not data[key]:
                return

    def _get(self, path: str, params: dict | list[tuple[str, str]]) -> dict:
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            resp = self._http.get(path, params=params)
            if resp.status_code == 429:
                self._interval = min(max(self._interval * 2, 0.1), _MAX_INTERVAL)
            elif resp.status_code < 500:
                self._interval = max(self._base_interval, self._interval * _RECOVERY)
                break
            time.sleep(_retry_delay(resp, attempt))
        resp.raise_for_status()
        return resp.json()

    def _throttle(self) -> None:
        wait = self._last_request + self._interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After when Kalshi sends it, else backoff."""
    try:
        return max(float(resp.headers["retry-after"]), 0.0)
    except (KeyError, ValueError):
        return 0.5 * 2**attempt
