"""The arbitrage scanner and locked arbitrage baskets, against a fake Kalshi with
one of each structure priced to break its rule (and a few that look broken but aren't)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.arb_models import ArbOpportunity, MarketTerms
from app.brokers import kalshi_fee_cents, to_cents
from app.config import Settings
from app.db import Base, make_engine
from app.kalshi import MarketData, OrderBook
from app.main import create_app
from app.models import Market, Stake, utcnow
from app.services import arbitrage, automation
from tests.conftest import TEST_DATABASE_URL, signup
from tests.fake_kalshi import FakeKalshi

PAPER = "/accounts/paper"
FEE = 0.07



def _dt(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


def _md(ticker, event, question, bid, ask, closes, **terms) -> MarketData:
    rules = terms.pop("rules", None)
    return MarketData(
        external_id=ticker,
        question=question,
        yes_price=round((bid + ask) / 2, 4),
        volume=50_000,
        category="Economics",
        closes_at=_dt(closes),
        event_ticker=event,
        yes_bid=bid,
        yes_ask=ask,
        resolution_rules=rules,
        resolution_deadline=_dt(closes) if rules else None,
        **terms,
    )


def _book(yes_bid, yes_ask, size=100.0) -> OrderBook:
    """One level each side. A YES ask at p rests as a NO bid at 1 - p."""
    return OrderBook(yes_bids=((yes_bid, size),), no_bids=((round(1 - yes_ask, 4), size),))


LADDER = "ARB-FED-26DEC"
LOW, HIGH = f"{LADDER}-T3.75", f"{LADDER}-T4.00"
FED_Q = "Fed funds rate after the December 2026 meeting?"
FED_RULES = "If the upper bound of the federal funds rate is greater than {}% following the Dec 9, 2026 meeting, then the market resolves to Yes."
STAR_RULES = "If exactly {} Starship launches reach space in 2026, then the market resolves to Yes."
CLIMATE_RULES = "If the US has {} million metric tonnes of CO2 emissions or fewer in a year by {}, then the market resolves to Yes."


def _fed(strike, bid, ask):
    return _md(f"{LADDER}-T{strike}", LADDER, FED_Q, bid, ask, "2026-12-10", strike_type="greater",
               floor_strike=float(strike), outcome=f"Above {strike}%", rules=FED_RULES.format(strike))


MARKETS = [
    # Ladder: "above 4.00%" is bid at 47c while "above 3.75%" is offered at 42c.
    _fed("3.75", 0.40, 0.42),
    _fed("4.00", 0.47, 0.49),
    _fed("4.25", 0.20, 0.22),
    # Look like ladders priced backwards, but aren't. Kalshi labels "exactly 4" and "exactly 9" as
    # "less" with floor = cap, in a mutually exclusive event: they exclude each other, they don't nest.
    *[
        _md(f"ARB-STAR-26-{n}.0", "ARB-STAR-26", f"Will exactly {n} Starship launches reach space?", bid, ask,
            "2027-01-01", strike_type="less", floor_strike=float(n), cap_strike=float(n), outcome=str(n),
            mutually_exclusive=True, event_size=2, rules=STAR_RULES.format(n))
        for n, bid, ask in ((4, 0.50, 0.58), (9, 0.01, 0.02))
    ],
    # One event, two different goals: emissions under one line by 2025, under another by 2030.
    *[
        _md(f"ARB-CLIM-{year}", "ARB-CLIM", "US meets its climate goals?", bid, ask, f"{year + 5}-12-31",
            strike_type="less_or_equal", cap_strike=cap, outcome=f"By {year}", rules=CLIMATE_RULES.format(cap, year))
        for year, cap, bid, ask in ((2025, 4909.9, 0.073, 0.129), (2030, 3317.5, 0.161, 0.196))
    ],
    # Mutually exclusive outcomes whose YES bids add up past $1: NO on all three pays at least $2.
    *[
        _md(f"ARB-NOM-28-{name}", "ARB-NOM-28", f"Will {name} be the 2028 nominee?", bid, ask, "2028-08-01",
            strike_type="custom", outcome=name, mutually_exclusive=True, event_size=3)
        for name, bid, ask in (("ALPHA", 0.55, 0.57), ("BETA", 0.50, 0.52), ("GAMMA", 0.03, 0.04))
    ],
    # Ranges covering every price, offered at 30c each: YES on all three costs 90c for exactly $1.
    _md("ARB-BTC-26DEC31-T100", "ARB-BTC-26DEC31", "BTC on Dec 31?", 0.28, 0.30, "2026-12-31",
        strike_type="less", cap_strike=100, outcome="Below 100", mutually_exclusive=True, event_size=3),
    _md("ARB-BTC-26DEC31-B105", "ARB-BTC-26DEC31", "BTC on Dec 31?", 0.28, 0.30, "2026-12-31",
        strike_type="between", floor_strike=100, cap_strike=109.99, outcome="100 to 109.99", mutually_exclusive=True, event_size=3),
    _md("ARB-BTC-26DEC31-T109.99", "ARB-BTC-26DEC31", "BTC on Dec 31?", 0.28, 0.30, "2026-12-31",
        strike_type="greater", floor_strike=109.99, outcome="110 or above", mutually_exclusive=True, event_size=3),
]

BOOKS = {
    # 200 YES at 42c, then 500 at 43c.
    LOW: OrderBook(yes_bids=((0.40, 300.0),), no_bids=((0.58, 200.0), (0.57, 500.0))),
    # NO at 53c for 150, then 54c for 400 (YES bids at 47c and 46c).
    HIGH: OrderBook(yes_bids=((0.47, 150.0), (0.46, 400.0)), no_bids=((0.51, 300.0),)),
    f"{LADDER}-T4.25": _book(0.20, 0.22),
    **{m.external_id: _book(m.yes_bid, m.yes_ask) for m in MARKETS if m.event_ticker != LADDER},
}


class ArbKalshi(FakeKalshi):
    """FakeKalshi plus the structured markets above, with fixed books."""

    def __init__(self):
        super().__init__(seed=0)
        self.books.update(BOOKS)

    def fetch_markets(self) -> list[MarketData]:
        return super().fetch_markets() + [m for m in MARKETS if m.external_id not in self.results]


@pytest.fixture
def app():
    if TEST_DATABASE_URL != "sqlite://":
        engine = make_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(engine)
        engine.dispose()
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        shared_database=":memory:",
        backfill_days=5,
        paper_quote_cache_seconds=0,
        live_refresh_seconds=0,
        _env_file=None,
    )
    return create_app(settings, ArbKalshi())


@pytest.fixture
def fake(app, client) -> ArbKalshi:
    return app.state.market_source


def _by_kind(client) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for o in client.get("/arbitrage").json()["opportunities"]:
        found.setdefault(o["kind"], []).append(o)
    return found


def _ladder(client) -> dict:
    [opp] = _by_kind(client)["ladder"]
    return opp


def _cents(fills) -> int:
    return to_cents(sum(p * q for p, q in fills), up=True) + kalshi_fee_cents(fills, FEE)


def _lock(client, opp_id, **body):
    return client.post(f"{PAPER}/arbitrage/{opp_id}/lock", json=body)


# ---- Terms -------------------------------------------------------------------------------------------


def test_strike_template_matches_rules_that_differ_only_in_their_strike():
    fed = [arbitrage.strike_template(FED_RULES.format(s), float(s), None) for s in ("3.75", "4.00", "4.25")]
    assert len(set(fed)) == 1
    by_2025 = arbitrage.strike_template(CLIMATE_RULES.format(4909.9, 2025), None, 4909.9)
    by_2030 = arbitrage.strike_template(CLIMATE_RULES.format(3317.5, 2030), None, 3317.5)
    assert by_2025 != by_2030
    # Thousands separators and trailing zeros still read as the strike.
    assert arbitrage.strike_template("above $67,500.00 at 5pm", 67500.0, None) == arbitrage.strike_template(
        "above $68,000 at 5pm", 68000.0, None
    )


def _rows(*spans, size=None):
    rows = []
    for strike_type, floor, cap in spans:
        terms = MarketTerms(strike_type=strike_type, floor_strike=floor, cap_strike=cap, event_size=size or len(spans))
        rows.append(arbitrage._Row(Market(question="Q", yes_price=0.3), terms))
    return rows


def test_ranges_must_cover_every_outcome():
    full = [("less", None, 100), ("between", 100, 109.99), ("greater", 109.99, None)]
    assert arbitrage._covers_everything(_rows(*full))
    # A range the volume filter dropped.
    assert not arbitrage._covers_everything(_rows(*full, size=4))
    # A hole between 110 and 120.
    assert not arbitrage._covers_everything(
        _rows(("less", None, 100), ("between", 100, 109.99), ("greater", 119.99, None))
    )
    # "Below 100" and "above 100" both miss exactly 100.
    assert not arbitrage._covers_everything(_rows(("less", None, 100), ("greater", 100, None)))
    # Whole-number ranges a unit apart do cover (seat counts).
    assert arbitrage._covers_everything(_rows(("less", None, 210), ("between", 210, 219), ("greater", 219, None)))
    # ...but "below 210" stops short of 210 when the next range starts at 211.
    assert not arbitrage._covers_everything(_rows(("less", None, 210), ("between", 211, 219), ("greater", 219, None)))
    # Kalshi's "exactly N" markets ("less" with floor = cap) cover only their own value.
    assert arbitrage._covers_everything(
        _rows(("less", None, 2), ("less", 2, 2), ("less", 3, 3), ("greater_or_equal", 4, None))
    )
    assert not arbitrage._covers_everything(_rows(("less", 2, 2), ("less", 3, 3)))


# ---- Scanning --------------------------------------------------------------------------------------


def test_scan_after_sync_finds_one_of_each_structure(client):
    found = _by_kind(client)
    assert sorted(found) == ["bracket_no", "bracket_yes", "ladder"]
    assert all(len(v) == 1 for v in found.values())
    assert all(o["confirmed"] and o["profit"] > 0 for v in found.values() for o in v)

    ladder = found["ladder"][0]
    assert [(leg["ticker"], leg["side"]) for leg in ladder["legs"]] == [(LOW, "yes"), (HIGH, "no")]
    nom = found["bracket_no"][0]
    assert {leg["side"] for leg in nom["legs"]} == {"no"} and len(nom["legs"]) == 3
    assert nom["payout_per_set"] == 2
    assert len(found["bracket_yes"][0]["legs"]) == 3

    summary = client.get("/arbitrage").json()["summary"]
    assert summary["scans"] == 1 and summary["active"] == 3 and summary["opportunities_seen"] == 3
    assert summary["profit_available"] == pytest.approx(sum(o["profit"] for v in found.values() for o in v))


def test_ladder_sized_at_the_most_profitable_depth(client):
    opp = _ladder(client)
    # 150 sets at 42c + 53c pay 1.6c each; 50 more at 42c + 54c still pay; past 200, YES costs 43c and they don't.
    assert opp["sets"] == 200
    cost = _cents([(0.42, 200)]) + _cents([(0.53, 150), (0.54, 50)])
    assert opp["cost"] == cost / 100
    assert opp["profit"] == pytest.approx((200 * 100 - cost) / 100)
    assert opp["edge_per_set"] == pytest.approx(1 - 0.42 - 0.53 - FEE * 0.42 * 0.58 - FEE * 0.53 * 0.47)
    assert opp["annualized"] > opp["return_pct"] > 0


def test_markets_that_share_an_event_but_dont_nest_are_no_ladder(client):
    """Both priced "backwards"; neither is a ladder (see the fixtures)."""
    legs = {leg["ticker"] for v in _by_kind(client).values() for o in v for leg in o["legs"]}
    assert not {t for t in legs if t.startswith(("ARB-STAR", "ARB-CLIM"))}
    assert _ladder(client)["title"] == FED_Q


def test_rescan_retires_what_closed_and_keeps_the_log(client, fake):
    ladder_id = _ladder(client)["id"]
    fake.books[HIGH] = _book(0.38, 0.40)  # NO now costs 62c: the gap is gone
    body = client.post("/arbitrage/scan", json={}).json()
    assert ladder_id not in {o["id"] for o in body["opportunities"]}
    assert body["summary"]["scans"] == 2 and body["summary"]["opportunities_seen"] == 3
    assert body["summary"]["best_profit_seen"] > body["summary"]["profit_available"]


def test_scan_needs_sign_in(client):
    client.headers.pop("Authorization")
    assert client.get("/arbitrage").status_code == 401


# ---- Locking -----------------------------------------------------------------------------------------


def test_preview_then_lock_buys_matched_legs(client, session):
    opp = _ladder(client)
    preview = _lock(client, opp["id"], dry_run=True).json()
    assert preview["sets"] == 200 and preview["cost"] == opp["cost"] and preview["profit"] == pytest.approx(opp["profit"])
    assert [(o["side"], o["count"]) for o in preview["orders"]] == [("yes", 200), ("no", 200)]

    resp = _lock(client, opp["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    lock = body["lock"]
    assert body["filled_sets"] == 200
    assert (lock["sets"], lock["payout"], lock["status"], lock["held_sets"]) == (200, 200, "locked", 200)
    assert lock["cost"] == opp["cost"] and lock["profit"] > 0
    assert body["basket_order"]["origin"] == "arbitrage"
    assert body["account"]["cash"] == pytest.approx(10_000 - opp["cost"])

    basket = client.get(f"/baskets/{lock['basket_id']}").json()
    assert basket["lock"]["kind"] == "ladder" and basket["name"].startswith("Lock: ")
    held = next(b for b in body["account"]["baskets"] if b["basket_id"] == lock["basket_id"])
    assert sorted((h["market"]["external_id"], h["side"], h["contracts"]) for h in held["holdings"]) == [
        (LOW, "yes", 200),
        (HIGH, "no", 200),
    ]


def test_locking_again_adds_to_the_same_basket(client):
    opp = _ladder(client)
    first = _lock(client, opp["id"], sets=50).json()["lock"]
    second = _lock(client, opp["id"], sets=30).json()["lock"]
    assert second["basket_id"] == first["basket_id"]
    assert (second["sets"], second["held_sets"]) == (80, 80)
    assert len(client.get(f"{PAPER}/arbitrage/locks").json()) == 1


def test_lock_refused_once_the_gap_closes(client, fake):
    opp = _ladder(client)
    fake.books[HIGH] = _book(0.38, 0.40)
    resp = _lock(client, opp["id"])
    assert resp.status_code == 409 and "no longer pays" in resp.json()["detail"]
    # A size that doesn't pay is refused too, with what it would cost.
    fake.books[HIGH] = BOOKS[HIGH]
    resp = _lock(client, opp["id"], sets=600)
    assert resp.status_code == 409 and "at most 550" in resp.json()["detail"]
    assert client.get(PAPER).json()["cash"] == 10_000


def test_nothing_locked_when_a_leg_fills_short(client, fake, monkeypatch):
    opp = _ladder(client)
    full = fake.fetch_orderbooks

    def thin_when_buying_high(tickers):
        books = full(tickers)
        # The order for the NO leg finds only 120 contracts left at 53c.
        if tickers == [HIGH]:
            books[HIGH] = OrderBook(yes_bids=((0.47, 120.0),), no_bids=((0.51, 300.0),))
        return books

    monkeypatch.setattr(fake, "fetch_orderbooks", thin_when_buying_high)
    resp = _lock(client, opp["id"])
    assert resp.status_code == 409 and "nothing was locked" in resp.json()["detail"]
    assert client.get(PAPER).json()["cash"] == 10_000


def test_nothing_locked_when_a_leg_fills_nothing(client, fake, monkeypatch):
    opp = _ladder(client)
    full = fake.fetch_orderbooks

    def empty_high(tickers):
        books = full(tickers)
        if tickers == [HIGH]:
            books[HIGH] = OrderBook(yes_bids=(), no_bids=((0.51, 300.0),))
        return books

    monkeypatch.setattr(fake, "fetch_orderbooks", empty_high)
    resp = _lock(client, opp["id"])
    assert resp.status_code == 409 and "nothing was locked" in resp.json()["detail"]
    assert client.get(PAPER).json()["cash"] == 10_000
    assert not [b for b in client.get("/baskets").json() if b["lock"]]


def test_trades_by_weight_and_automations_are_refused_on_a_lock(client, session, app):
    basket_id = _lock(client, _ladder(client)["id"]).json()["lock"]["basket_id"]
    base = f"{PAPER}/baskets/{basket_id}"
    refused = [
        client.post(f"{base}/buy", json={"amount": 50}),
        client.post(f"{base}/rebalance", json={}),
        client.post(f"{base}/sell", json={"fraction": 0.5}),
        client.put(f"{base}/automation", json={"auto_rebalance_drift": 0.05}),
        client.post(f"{base}/conditional-orders", json={"action": "sell", "amount": 1, "condition": "at_or_above", "nav": 500}),
        client.put(f"/baskets/{basket_id}/climate/settings", json={"mode": "auto", "sensitivity": 0.5, "max_shift": 0.05}),
        client.post(f"/baskets/{basket_id}/rebalance"),
    ]
    assert [r.status_code for r in refused] == [409] * len(refused)
    assert "locked arbitrage" in refused[0].json()["detail"]

    # An automation set before the guard existed still never fires.
    stake = session.query(Stake).filter_by(basket_id=basket_id).one()
    stake.auto_rebalance_drift = 0.0001
    session.commit()
    assert automation.run(session, app.state.brokers, utcnow()) == 0

    # Selling all of it unwinds the lock, and its floor no longer counts as locked profit.
    assert client.post(f"{base}/sell", json={"fraction": 1}).status_code == 200
    [lock] = client.get(f"{PAPER}/arbitrage/locks").json()
    assert (lock["status"], lock["held_sets"]) == ("closed", 0)
    summary = client.get("/arbitrage").json()["summary"]
    assert (summary["locks"], summary["locked_payout"]) == (0, 0)


def test_locking_again_after_an_unwind_starts_the_totals_over(client):
    opp = _ladder(client)
    first = _lock(client, opp["id"], sets=50).json()["lock"]
    assert client.post(f"{PAPER}/baskets/{first['basket_id']}/sell", json={"fraction": 1}).status_code == 200
    again = _lock(client, opp["id"], sets=30).json()["lock"]
    assert again["basket_id"] == first["basket_id"]
    assert (again["sets"], again["payout"], again["held_sets"]) == (30, 30, 30)
    assert again["cost"] < first["cost"]


def test_settlement_pays_the_locked_floor(client, fake):
    lock = _lock(client, _ladder(client)["id"]).json()["lock"]
    # The rate lands between 3.75% and 4.00%: both legs win.
    fake.resolve(LOW, 1.0)
    fake.resolve(HIGH, 0.0)
    assert client.post("/markets/sync").json()["settled"] == 2
    [after] = client.get(f"{PAPER}/arbitrage/locks").json()
    assert after["status"] == "settled"
    account = client.get(PAPER).json()
    assert account["cash"] == pytest.approx(10_000 - lock["cost"] + 2 * 200)
    assert account["cash"] - 10_000 >= lock["profit"]


def test_lock_404_and_live_mode(client):
    assert _lock(client, "nope").status_code == 404
    assert client.post("/accounts/live/arbitrage/x/lock", json={}).status_code == 501
