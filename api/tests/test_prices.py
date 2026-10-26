import statistics

import pytest

from tests.fake_kalshi import FakeKalshi


def _first_market(client):
    return client.get("/markets").json()["items"][0]


def test_seed_backfills_daily_history_ending_at_current_price(client):
    market = _first_market(client)
    points = client.get(f"/markets/{market['id']}/prices").json()
    assert len(points) == 30 + 1
    timestamps = [p["ts"] for p in points]
    assert timestamps == sorted(timestamps)
    assert points[-1]["yes_price"] == market["yes_price"]


def test_sync_appends_a_price_point_and_moves_prices(client):
    before = {m["id"]: m["yes_price"] for m in client.get("/markets").json()["items"]}
    client.post("/markets/sync")
    after = {m["id"]: m["yes_price"] for m in client.get("/markets").json()["items"]}
    assert before != after

    market_id = next(iter(after))
    points = client.get(f"/markets/{market_id}/prices").json()
    assert len(points) == 30 + 2
    assert points[-1]["yes_price"] == after[market_id]


def test_prices_limit_and_since(client):
    market_id = _first_market(client)["id"]
    points = client.get(f"/markets/{market_id}/prices").json()
    assert client.get(f"/markets/{market_id}/prices", params={"limit": 5}).json() == points[-5:]
    since = points[-3]["ts"]
    assert client.get(f"/markets/{market_id}/prices", params={"since": since}).json() == points[-3:]


def test_prices_unknown_market_404(client):
    assert client.get("/markets/9999/prices").status_code == 404


def _price_changes(path, external_id):
    series = [snapshot[external_id] for snapshot in path]
    return [b - a for a, b in zip(series, series[1:])]


def test_fake_markets_sharing_a_factor_are_correlated():
    path = FakeKalshi(seed=1).backfill(500)
    fed = _price_changes(path, "MOCK-FEDCUT-26DEC")
    assert statistics.correlation(fed, _price_changes(path, "MOCK-CPI-26NOV-B3")) > 0.5
    assert abs(statistics.correlation(fed, _price_changes(path, "MOCK-BTC-26DEC31-150K"))) < 0.2
    house = _price_changes(path, "MOCK-HOUSE-26-D")
    assert statistics.correlation(house, _price_changes(path, "MOCK-SENATE-26-R")) < -0.5


def test_basket_nav_recorded_at_creation_and_on_sync(client):
    ids = [m["id"] for m in client.get("/markets", params={"limit": 3}).json()["items"]]
    basket = client.post("/baskets", json={"name": "B", "positions": [{"market_id": i} for i in ids]}).json()
    navs = client.get(f"/baskets/{basket['id']}/nav").json()
    assert [p["nav"] for p in navs] == [pytest.approx(100)]

    client.post("/markets/sync")
    navs = client.get(f"/baskets/{basket['id']}/nav").json()
    assert len(navs) == 2
    assert navs[-1]["nav"] == pytest.approx(client.get(f"/baskets/{basket['id']}").json()["nav"])


def test_basket_nav_unknown_basket_404(client):
    assert client.get("/baskets/9999/nav").status_code == 404
