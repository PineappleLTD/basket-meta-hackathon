import math

import pytest

from app.brokers import kalshi_fee
from app.models import Market
from tests.conftest import signup

PAPER = "/accounts/paper"
FEE_RATE = 0.07


def _basket(client, n=2, side="yes", name="Trading basket"):
    ids = [m["id"] for m in client.get("/markets", params={"limit": n}).json()["items"]]
    resp = client.post("/baskets", json={"name": name, "positions": [{"market_id": i, "side": side} for i in ids]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _buy(client, basket_id, amount, **extra):
    return client.post(f"{PAPER}/baskets/{basket_id}/buy", json={"amount": amount, **extra})


def _sell(client, basket_id, fraction=1.0, **extra):
    return client.post(f"{PAPER}/baskets/{basket_id}/sell", json={"fraction": fraction, **extra})


@pytest.mark.parametrize(
    "count, price, fee",
    [
        (100, 0.5, 1.75),
        (1, 0.5, 0.02),  # 1.75 cents rounds up
        (100, 0.01, 0.07),
        (0, 0.5, 0.0),
    ],
)
def test_kalshi_fee(count, price, fee):
    assert kalshi_fee(count, price, FEE_RATE) == pytest.approx(fee)


def test_paper_account_opens_with_starting_cash(client):
    account = client.get(PAPER).json()
    assert account["cash"] == account["equity"] == account["starting_cash"] == 10_000
    assert account["pnl"] == 0
    assert account["baskets"] == []
    assert len(client.get(f"{PAPER}/equity").json()) == 1


def test_live_trading_is_not_available(client):
    assert client.get("/accounts/live").status_code == 501


def test_accounts_need_sign_in(client):
    client.headers.pop("Authorization")
    assert client.get(PAPER).status_code == 401


def test_dry_run_prices_orders_without_placing_them(client, session):
    basket = _basket(client)
    preview = _buy(client, basket["id"], 1000, dry_run=True).json()

    assert preview["kind"] == "buy"
    for order in preview["orders"]:
        market = session.get(Market, order["market_id"])
        assert order["action"] == "buy"
        assert order["price"] == pytest.approx(market.yes_ask)
        assert order["fee"] == pytest.approx(kalshi_fee(order["count"], order["price"], FEE_RATE))
    assert preview["cash_after"] == pytest.approx(10_000 + preview["cash_delta"])

    assert client.get(PAPER).json()["cash"] == 10_000
    assert client.get(f"{PAPER}/orders").json() == []


def test_buy_fills_at_the_ask_and_spends_at_most_the_amount(client, session):
    basket = _basket(client)
    result = _buy(client, basket["id"], 1000)
    assert result.status_code == 200, result.text
    body = result.json()

    spent = 0.0
    for order in body["basket_order"]["orders"]:
        market = session.get(Market, order["market_id"])
        assert order["filled_count"] == order["count"] > 0
        assert order["fill_price"] == pytest.approx(market.yes_ask)
        cost = order["count"] * order["fill_price"] + order["fee"]
        # Each leg gets its weight's share of the amount, fees included.
        assert cost <= 500 + 1e-9
        # One more contract wouldn't have fit.
        more = order["count"] + 1
        assert more * order["fill_price"] + kalshi_fee(more, order["fill_price"], FEE_RATE) > 500
        spent += cost

    account = body["account"]
    assert account["cash"] == pytest.approx(10_000 - spent)
    held = account["baskets"][0]
    assert held["basket_id"] == basket["id"]
    assert held["cost"] == pytest.approx(spent)
    # Marked at the mid, so paying the ask plus fees starts slightly down.
    assert account["equity"] < 10_000
    assert held["unrealized_pnl"] < 0
    for h in held["holdings"]:
        assert h["target_weight"] == pytest.approx(0.5)
        assert abs(h["drift"]) < 0.01


def test_buying_no_pays_one_minus_the_yes_bid(client, session):
    basket = _basket(client, n=1, side="no")
    order = _buy(client, basket["id"], 100).json()["basket_order"]["orders"][0]
    assert order["side"] == "no"
    assert order["fill_price"] == pytest.approx(1 - session.get(Market, order["market_id"]).yes_bid)


@pytest.mark.parametrize("amount, error", [(20_000, "Not enough cash"), (0.05, "too small")])
def test_buy_rejected(client, amount, error):
    basket = _basket(client)
    resp = _buy(client, basket["id"], amount)
    assert resp.status_code == 409
    assert error in resp.json()["detail"]


def test_buy_rejected_when_a_leg_has_no_ask(client, session):
    basket = _basket(client)
    session.get(Market, basket["positions"][0]["market"]["id"]).yes_ask = None
    session.commit()
    resp = _buy(client, basket["id"], 1000)
    assert resp.status_code == 409
    assert "No quote" in resp.json()["detail"]
    assert client.get(PAPER).json()["cash"] == 10_000


def test_cannot_trade_another_users_basket(app, client):
    basket = _basket(client)
    client.headers.update(signup(client, email="other@example.com"))
    assert _buy(client, basket["id"], 100).status_code == 404


def test_sell_everything_returns_cash_and_realizes_pnl(client, session):
    basket = _basket(client)
    bought = _buy(client, basket["id"], 1000).json()
    cost = bought["account"]["baskets"][0]["cost"]

    sold = _sell(client, basket["id"]).json()
    proceeds = 0.0
    for order in sold["basket_order"]["orders"]:
        assert order["fill_price"] == pytest.approx(session.get(Market, order["market_id"]).yes_bid)
        proceeds += order["count"] * order["fill_price"] - order["fee"]

    account = sold["account"]
    assert account["baskets"] == []
    assert account["holdings_value"] == 0
    assert account["cash"] == pytest.approx(10_000 - cost + proceeds)
    realized = sum(o["realized_pnl"] for o in sold["basket_order"]["orders"])
    assert realized == pytest.approx(proceeds - cost)
    # A round trip pays the spread and two fees.
    assert account["pnl"] == pytest.approx(realized) and realized < 0

    history = client.get(f"{PAPER}/orders").json()
    assert [o["kind"] for o in history] == ["sell", "buy"]


def test_partial_sell_rounds_down(client):
    basket = _basket(client)
    held = {
        h["market"]["id"]: h["contracts"]
        for h in _buy(client, basket["id"], 1000).json()["account"]["baskets"][0]["holdings"]
    }
    orders = _sell(client, basket["id"], fraction=0.5).json()["basket_order"]["orders"]
    for order in orders:
        assert order["count"] == math.floor(held[order["market_id"]] * 0.5)


def test_sell_without_holdings_conflicts(client):
    basket = _basket(client)
    assert _sell(client, basket["id"]).status_code == 409


def test_sell_leaves_closed_markets_held(client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    closed_id = basket["positions"][0]["market"]["id"]
    session.get(Market, closed_id).is_open = False
    session.commit()

    sold = _sell(client, basket["id"]).json()
    assert sold["skipped"] == [{"market_id": closed_id, "side": "yes", "reason": "market is closed"}]
    remaining = sold["account"]["baskets"][0]["holdings"]
    assert [h["market"]["id"] for h in remaining] == [closed_id]


def test_rebalance_trades_holdings_back_to_target(client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 2000)
    a, b = (p["market"]["id"] for p in basket["positions"])

    market = session.get(Market, a)
    market.yes_price, market.yes_bid, market.yes_ask = market.yes_price / 2, market.yes_bid / 2, market.yes_ask / 2
    session.commit()
    drifted = client.get(PAPER).json()["baskets"][0]
    assert drifted["max_drift"] > 0.1

    preview = client.post(f"{PAPER}/baskets/{basket['id']}/rebalance", json={"dry_run": True}).json()
    assert [(o["market_id"], o["action"]) for o in preview["orders"]] == [(b, "sell"), (a, "buy")]

    result = client.post(f"{PAPER}/baskets/{basket['id']}/rebalance", json={})
    assert result.status_code == 200, result.text
    rebalanced = result.json()["account"]["baskets"][0]
    assert rebalanced["max_drift"] < 0.01
    assert result.json()["basket_order"]["kind"] == "rebalance"

    again = client.post(f"{PAPER}/baskets/{basket['id']}/rebalance", json={"dry_run": True})
    assert again.status_code == 409


def test_basket_with_holdings_cannot_be_deleted_until_sold(client):
    basket = _basket(client)
    _buy(client, basket["id"], 100)
    assert client.delete(f"/baskets/{basket['id']}").status_code == 409

    _sell(client, basket["id"])
    assert client.delete(f"/baskets/{basket['id']}").status_code == 204
    # Order history outlives the basket.
    history = client.get(f"{PAPER}/orders").json()
    assert {(o["basket_id"], o["basket_name"]) for o in history} == {(None, "Trading basket")}


def test_reset_restores_starting_cash_and_clears_history(client):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    account = client.post(f"{PAPER}/reset").json()
    assert account["cash"] == 10_000
    assert account["baskets"] == []
    assert account["reset_at"] is not None
    assert client.get(f"{PAPER}/orders").json() == []
    assert len(client.get(f"{PAPER}/equity").json()) == 1


def test_sync_records_account_equity(client):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    assert client.post("/markets/sync").status_code == 200
    # Opening, the buy, and the sync.
    points = client.get(f"{PAPER}/equity").json()
    assert len(points) == 3
    assert points[-1]["equity"] == pytest.approx(client.get(PAPER).json()["equity"])
