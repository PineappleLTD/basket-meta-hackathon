from sqlalchemy import func, select

from app.models import Market, MarketPrice
from app.services.sync import refresh_live
from tests.test_trading import PAPER, _basket, _buy, _ids


def _move(app, session, market_id, price):
    """Move a market's price at the source only, as Kalshi does between syncs."""
    app.state.market_source._prices[session.get(Market, market_id).external_id] = price


def _refresh(app, session):
    return refresh_live(session, app.state.market_source, app.state.brokers)


def test_live_refresh_reprices_only_markets_in_use_without_adding_history(app, client, session):
    basket = _basket(client, n=2)
    used, unused = _ids(basket)[0], client.get("/markets", params={"limit": 3}).json()["items"][2]["id"]
    before = session.get(Market, unused).yes_price
    _move(app, session, used, 0.33)
    _move(app, session, unused, 0.44)
    history = session.scalar(select(func.count()).select_from(MarketPrice))

    assert _refresh(app, session) == {"quoted": 2, "automated": 0}

    session.expire_all()
    assert session.get(Market, used).yes_price == 0.33
    assert session.get(Market, unused).yes_price == before
    assert session.scalar(select(func.count()).select_from(MarketPrice)) == history
    assert client.get(f"/baskets/{basket['id']}").json()["positions"][0]["price"] == 0.33


def test_stop_loss_fires_on_a_live_refresh(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    body = {"action": "sell", "amount": 1, "condition": "at_or_below", "nav": nav * 0.9}
    client.post(f"{PAPER}/baskets/{basket['id']}/conditional-orders", json=body)

    for market_id in _ids(basket):
        _move(app, session, market_id, session.get(Market, market_id).yes_price * 0.7)
    assert _refresh(app, session)["automated"] == 1
    assert client.get(f"{PAPER}/conditional-orders").json()[0]["status"] == "filled"


def test_a_new_basket_starts_from_live_prices(app, client, session):
    market_id = client.get("/markets", params={"limit": 1}).json()["items"][0]["id"]
    _move(app, session, market_id, 0.21)
    resp = client.post("/baskets", json={"name": "Live", "positions": [{"market_id": market_id, "side": "yes"}]})
    assert resp.json()["positions"][0]["price"] == 0.21


def test_a_new_basket_falls_back_to_stored_prices_when_kalshi_is_down(app, client, monkeypatch):
    def down(tickers):
        raise ConnectionError("Kalshi is down")

    monkeypatch.setattr(app.state.market_source, "fetch_quotes", down)
    market = client.get("/markets", params={"limit": 1}).json()["items"][0]
    resp = client.post("/baskets", json={"name": "Stale", "positions": [{"market_id": market["id"], "side": "yes"}]})
    assert resp.status_code == 201
    assert resp.json()["positions"][0]["price"] == market["yes_price"]
