import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.kalshi import KalshiClient, MarketData
from app.models import Market, MarketPrice, utcnow
from app.services import sync
from app.services.sync import sync_markets


def _market(ticker, bid="0.4000", ask="0.4400", last="0.4100", volume="50000.00", status="active"):
    return {
        "ticker": ticker,
        "event_ticker": f"EV-{ticker}",
        "title": f"Will {ticker} happen?",
        "status": status,
        "volume_fp": volume,
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "last_price_dollars": last,
        "close_time": "2027-01-01T00:00:00Z",
    }


def _event(category, *markets):
    return {"category": category, "title": "Event", "series_ticker": "KXSERIES", "markets": list(markets)}


def _client(handler, requests_per_second=0) -> KalshiClient:
    http = httpx.Client(base_url="https://kalshi.test", transport=httpx.MockTransport(handler))
    return KalshiClient(
        min_volume=10_000, exclude_categories=["Sports"], http=http, requests_per_second=requests_per_second
    )


def test_fetch_markets_pages_and_filters():
    pages = {
        None: {"cursor": "p2", "events": [
            _event("Economics", _market("MID"), _market("THIN", volume="9999.00")),
            _event("Sports", _market("GAME")),
        ]},
        "p2": {"cursor": "", "events": [
            _event("Politics", _market("ONESIDED", bid="0.0000", ask="0.0300", last="0.0200"),
                   _market("DONE", status="finalized")),
        ]},
    }  # fmt: skip

    def handler(request):
        assert request.url.path == "/events"
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    markets = {m.external_id: m for m in _client(handler).fetch_markets()}
    assert set(markets) == {"MID", "ONESIDED"}
    assert markets["MID"].yes_price == pytest.approx(0.42)  # mid of 0.40 / 0.44
    assert markets["ONESIDED"].yes_price == pytest.approx(0.02)  # no bid -> last trade
    assert markets["MID"].category == "Economics"
    assert markets["MID"].url == "https://kalshi.com/markets/kxseries"
    assert markets["MID"].closes_at.tzinfo is not None


def test_untitled_market_named_from_event():
    market = _market("KIMI") | {"title": "", "yes_sub_title": "Kimi"}
    event = _event("AI", market) | {"title": "Best AI in Oct 2026?"}
    client = _client(lambda _: httpx.Response(200, json={"cursor": "", "events": [event]}))
    assert client.fetch_markets()[0].question == "Best AI in Oct 2026?: Kimi"


def test_fetch_history_batches_and_skips_open_candle():
    now = int(time.time())
    calls = []

    def handler(request):
        tickers = request.url.params["market_tickers"].split(",")
        calls.append(len(tickers))
        candles = [
            {"end_period_ts": now - 86_400, "yes_bid": {"close_dollars": "0.3000"},
             "yes_ask": {"close_dollars": "0.3200"}, "price": {}},
            {"end_period_ts": now - 3_600, "yes_bid": {}, "yes_ask": {}, "price": {"previous_dollars": "0.3"}},
            {"end_period_ts": now + 3_600, "yes_bid": {"close_dollars": "0.5000"},
             "yes_ask": {"close_dollars": "0.5000"}, "price": {}},
        ]  # fmt: skip
        return httpx.Response(200, json={"markets": [{"market_ticker": t, "candlesticks": candles} for t in tickers]})

    history = _client(handler).fetch_history([f"T{i}" for i in range(150)], days=30)
    assert calls == [100, 50]
    assert len(history) == 150
    # Day with a quote kept, day with no quote or trade dropped, still-open day dropped.
    assert [p for _, p in history["T0"]] == [pytest.approx(0.31)]


def test_retries_rate_limit(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    responses = iter([httpx.Response(429), httpx.Response(200, json={"cursor": "", "events": []})])
    assert _client(lambda _: next(responses)).fetch_markets() == []


def test_retry_honors_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(503),
        httpx.Response(200, json={"cursor": "", "events": []}),
    ])  # fmt: skip
    assert _client(lambda _: next(responses)).fetch_markets() == []
    # Retry-After, then backoff for the second attempt (the rest is pacing after the 429).
    assert [s for s in sleeps if s >= 1] == [3.0, 1.0]


def test_requests_are_spaced_to_the_rate_cap(monkeypatch):
    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "sleep", sleep)
    pages = {None: {"cursor": "p2", "events": [_event("AI")]}, "p2": {"cursor": "p3", "events": [_event("AI")]},
             "p3": {"cursor": "", "events": [_event("AI")]}}  # fmt: skip
    client = _client(lambda r: httpx.Response(200, json=pages[r.url.params.get("cursor")]), requests_per_second=4)
    client.fetch_markets()
    # The first request goes out at once; each later one waits out the 0.25s interval.
    assert sleeps == [pytest.approx(0.25), pytest.approx(0.25)]


def test_rate_limit_slows_later_requests(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    responses = iter([httpx.Response(429), httpx.Response(200, json={"cursor": "", "events": []})])
    client = _client(lambda _: next(responses), requests_per_second=10)
    client.fetch_markets()
    assert client._interval == pytest.approx(0.2 * 0.9)  # doubled by the 429, eased by the success


def test_fetch_markets_drops_repeated_tickers():
    pages = {
        None: {"cursor": "p2", "events": [_event("Economics", _market("DUP", last="0.3000"))]},
        "p2": {"cursor": "", "events": [_event("Economics", _market("DUP"), _market("OTHER"))]},
    }
    client = _client(lambda r: httpx.Response(200, json=pages[r.url.params.get("cursor")]))
    assert sorted(m.external_id for m in client.fetch_markets()) == ["DUP", "OTHER"]


def test_fetch_orderbooks_batches_tickers_and_orders_bids_best_first(monkeypatch):
    monkeypatch.setattr("app.kalshi.client._LOOKUP_BATCH", 2)
    calls = []

    def handler(request):
        tickers = request.url.params.get_list("tickers")
        calls.append(tickers)
        book = {"yes_dollars": [["0.3000", "10.00"], ["0.3200", "5.50"]], "no_dollars": [["0.6500", "0.00"]]}
        return httpx.Response(200, json={"orderbooks": [{"ticker": t, "orderbook_fp": book} for t in tickers]})

    books = _client(handler).fetch_orderbooks(["A", "B", "C"])
    assert calls == [["A", "B"], ["C"]]
    # Kalshi lists bids lowest first; empty levels are dropped.
    assert books["A"].yes_bids == ((0.32, 5.5), (0.30, 10.0))
    assert books["A"].no_bids == ()


def test_fetch_results_keeps_only_resolved_markets():
    def handler(request):
        assert request.url.params["tickers"] == "Y,N,OPEN,SCALAR"
        return httpx.Response(
            200,
            json={
                "markets": [
                    {"ticker": "Y", "status": "finalized", "result": "yes", "settlement_value_dollars": "1.0000"},
                    {"ticker": "N", "status": "determined", "result": "no"},
                    {"ticker": "OPEN", "status": "active", "result": ""},
                    {"ticker": "SCALAR", "status": "settled", "result": "scalar", "settlement_value_dollars": "0.3700"},
                ]
            },
        )

    assert _client(handler).fetch_results(["Y", "N", "OPEN", "SCALAR"]) == {"Y": 1.0, "N": 0.0, "SCALAR": 0.37}


class _FakeSource:
    def __init__(self, markets, history=None):
        self.markets = markets
        self.history = history or {}
        self.history_calls = []

    def fetch_markets(self):
        return self.markets

    def fetch_history(self, tickers, days):
        self.history_calls.append(len(tickers))
        return {t: self.history[t] for t in tickers if t in self.history}


def test_sync_closes_markets_the_source_stops_returning(session):
    a, b = (MarketData(external_id=t, question=t, yes_price=0.5) for t in ("NEW-A", "NEW-B"))
    # The 11 seeded fake markets aren't returned, so they close.
    counts = sync_markets(session, _FakeSource([a, b]), backfill_days=0)
    assert counts == {"created": 2, "updated": 0, "closed": 11, "resolved": 0, "settled": 0}
    counts = sync_markets(session, _FakeSource([a]), backfill_days=0)
    assert counts == {"created": 0, "updated": 1, "closed": 1, "resolved": 0, "settled": 0}


def test_sync_stores_a_repeated_ticker_once(session):
    first = MarketData(external_id="DUP", question="old", yes_price=0.4)
    last = MarketData(external_id="DUP", question="new", yes_price=0.6)
    yesterday = utcnow() - timedelta(days=1)
    source = _FakeSource([first, last], history={"DUP": [(yesterday, 0.5), (yesterday, 0.5)]})
    assert sync_markets(session, source, backfill_days=30)["created"] == 1
    market = session.scalars(select(Market).where(Market.external_id == "DUP")).one()
    assert (market.question, market.yes_price) == ("new", 0.6)
    prices = session.scalars(select(MarketPrice.yes_price).where(MarketPrice.market_id == market.id)).all()
    assert sorted(prices) == [0.5, 0.6]


def test_sync_backfills_new_markets_in_chunks(session, monkeypatch):
    monkeypatch.setattr(sync, "_BACKFILL_CHUNK", 2)
    source = _FakeSource([MarketData(external_id=f"N{i}", question="q", yes_price=0.5) for i in range(5)])
    sync_markets(session, source, backfill_days=30)
    assert source.history_calls == [2, 2, 1]
