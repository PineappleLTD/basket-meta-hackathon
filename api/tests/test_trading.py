import math
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm.exc import StaleDataError

from app.brokers import PaperBroker, kalshi_fee_cents
from app.kalshi import OrderBook
from app.models import Account, Action, BasketPosition, Market, Side, utcnow
from app.services import trading
from tests.conftest import signup
from tests.fake_kalshi import BOOK_DEPTH

PAPER = "/accounts/paper"
FEE_RATE = 0.07


def _basket(client, n=2, side="yes", name="Trading basket"):
    ids = [m["id"] for m in client.get("/markets", params={"limit": n}).json()["items"]]
    resp = client.post("/baskets", json={"name": name, "positions": [{"market_id": i, "side": side} for i in ids]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ids(basket):
    return [p["market"]["id"] for p in basket["positions"]]


def _buy(client, basket_id, amount, **extra):
    return client.post(f"{PAPER}/baskets/{basket_id}/buy", json={"amount": amount, **extra})


def _sell(client, basket_id, fraction=1.0, **extra):
    return client.post(f"{PAPER}/baskets/{basket_id}/sell", json={"fraction": fraction, **extra})


def _rebalance(client, basket_id, **extra):
    return client.post(f"{PAPER}/baskets/{basket_id}/rebalance", json=extra)


def _held(account, basket_id):
    return next(b for b in account["baskets"] if b["basket_id"] == basket_id)


def _fake(app):
    return app.state.market_source


def _set_price(app, session, market_id, price):
    """Move a market's price in the fake's book and in the stored quotes."""
    market = session.get(Market, market_id)
    fake = _fake(app)
    fake._prices[market.external_id] = price
    market.yes_price = price
    market.yes_bid, market.yes_ask = fake._quotes(market.external_id)
    session.commit()


@pytest.mark.parametrize(
    "fills, cents",
    [
        ([(0.5, 100)], 175),
        ([(0.5, 1)], 2),  # 1.75 cents rounds up
        ([(0.01, 100)], 7),
        ([(0.5, 50), (0.5, 50)], 175),  # rounded once per order, not per level
        ([], 0),
    ],
)
def test_kalshi_fee(fills, cents):
    assert kalshi_fee_cents(fills, FEE_RATE) == cents


def test_paper_account_opens_with_starting_cash(client):
    account = client.get(PAPER).json()
    assert account["cash"] == account["equity"] == account["starting_cash"] == account["liquidation_equity"] == 10_000
    assert account["pnl"] == account["realized_pnl"] == 0
    assert account["baskets"] == []
    assert len(client.get(f"{PAPER}/equity").json()) == 1


def test_live_trading_is_not_available(client):
    assert client.get("/accounts/live").status_code == 501


def test_accounts_need_sign_in(client):
    client.headers.pop("Authorization")
    assert client.get(PAPER).status_code == 401


def test_dry_run_prices_orders_against_the_book_without_placing_them(client, session):
    basket = _basket(client)
    preview = _buy(client, basket["id"], 1000, dry_run=True).json()

    assert preview["kind"] == "buy"
    for order in preview["orders"]:
        market = session.get(Market, order["market_id"])
        assert order["action"] == "buy"
        assert order["best_price"] == pytest.approx(market.yes_ask)
        assert order["cash_delta"] == pytest.approx(-(order["price"] * order["count"] + order["fee"]), abs=0.01)
    assert preview["fees"] == pytest.approx(sum(o["fee"] for o in preview["orders"]))
    assert preview["cash_after"] == pytest.approx(10_000 + preview["cash_delta"])

    assert client.get(PAPER).json()["cash"] == 10_000
    assert client.get(f"{PAPER}/orders").json() == []


def test_buy_spends_at_most_the_amount_and_leaves_less_than_a_contract(client, session):
    basket = _basket(client)
    # Small enough that each leg fills inside the top level of the book.
    result = _buy(client, basket["id"], 400)
    assert result.status_code == 200, result.text
    body = result.json()

    spent = 0.0
    for order in body["basket_order"]["orders"]:
        market = session.get(Market, order["market_id"])
        assert order["filled_count"] == order["count"] > 0
        assert order["fill_price"] == pytest.approx(market.yes_ask)
        spent += order["gross"] + order["fee"]
    assert spent <= 400
    # Leftover cash went into extra contracts, so less than one more of either leg's would fit.
    cheapest = min(session.get(Market, i).yes_ask for i in _ids(basket))
    assert 400 - spent < cheapest + 0.05

    account = body["account"]
    assert account["cash"] == pytest.approx(10_000 - spent)
    held = _held(account, basket["id"])
    assert held["cost"] == pytest.approx(spent)
    # Marked at the mid, so paying the ask plus fees starts slightly down, and lower still at the bids.
    assert account["equity"] < 10_000
    assert account["liquidation_equity"] < account["equity"]
    assert held["liquidation_value"] < held["value"]
    assert held["unrealized_pnl"] < 0
    for h in held["holdings"]:
        assert h["target_weight"] == pytest.approx(0.5)
        assert abs(h["drift"]) < 0.01


def test_a_big_buy_walks_the_book(client, session):
    basket = _basket(client, n=1)
    market = session.get(Market, _ids(basket)[0])
    # More than the top level holds, so it takes the next levels too.
    amount = market.yes_ask * BOOK_DEPTH * 2.5
    order = _buy(client, basket["id"], amount, dry_run=True).json()["orders"][0]
    assert order["best_price"] == pytest.approx(market.yes_ask)
    assert order["limit_price"] == pytest.approx(market.yes_ask + 0.02)
    assert order["price"] > order["best_price"]
    assert order["price_impact"] > 0


def test_buy_mirrors_the_index_current_weights(app, client, session):
    basket = _basket(client)
    a, b = _ids(basket)
    # A's price rises, so the index now holds more A than its 50% target.
    _set_price(app, session, a, session.get(Market, a).yes_price * 1.3)
    weights = {p["market"]["id"]: p["current_weight"] for p in client.get(f"/baskets/{basket['id']}").json()["positions"]}
    assert weights[a] > 0.53

    held = _held(_buy(client, basket["id"], 2000).json()["account"], basket["id"])
    for h in held["holdings"]:
        assert h["weight"] == pytest.approx(weights[h["market"]["id"]], abs=0.01)


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


def test_buy_skips_a_leg_with_no_asks_and_buys_the_rest(app, client, session):
    basket = _basket(client)
    a, b = _ids(basket)
    ticker = session.get(Market, a).external_id
    _fake(app).books[ticker] = OrderBook(yes_bids=((0.4, 100.0),), no_bids=())

    body = _buy(client, basket["id"], 1000).json()
    assert body["skipped"] == [{"market_id": a, "side": "yes", "reason": "no asks"}]
    assert [o["market_id"] for o in body["basket_order"]["orders"]] == [b]
    # A's half stays as cash rather than overweighting B.
    assert body["account"]["cash"] > 10_000 - 501


def test_a_thin_book_buys_what_is_on_offer(app, client, session):
    basket = _basket(client, n=1)
    market = session.get(Market, _ids(basket)[0])
    _fake(app).books[market.external_id] = OrderBook(yes_bids=(), no_bids=((1 - market.yes_ask, 10.0),))
    body = _buy(client, basket["id"], 1000).json()
    assert body["basket_order"]["orders"][0]["filled_count"] == 10
    assert "only 10 contracts on offer" in body["skipped"][0]["reason"]


def test_without_a_live_book_a_fresh_stored_quote_stands_in(app, client, session):
    basket = _basket(client, n=1)
    _fake(app).books_error = httpx.ConnectError("down")
    market = session.get(Market, _ids(basket)[0])

    order = _buy(client, basket["id"], 100, dry_run=True).json()["orders"][0]
    assert order["best_price"] == pytest.approx(market.yes_ask)

    market.quoted_at = utcnow() - timedelta(hours=3)
    session.commit()
    resp = _buy(client, basket["id"], 100, dry_run=True)
    assert resp.status_code == 409
    assert "no live quote, and the last one is 3 hours old" in resp.json()["detail"]


def test_cannot_trade_another_users_basket(app, client):
    basket = _basket(client)
    client.headers.update(signup(client, email="other@example.com"))
    assert _buy(client, basket["id"], 100).status_code == 404


def test_sell_everything_returns_cash_and_realizes_pnl(client, session):
    basket = _basket(client)
    bought = _buy(client, basket["id"], 400).json()
    cost = _held(bought["account"], basket["id"])["cost"]

    sold = _sell(client, basket["id"]).json()
    proceeds = 0.0
    for order in sold["basket_order"]["orders"]:
        assert order["fill_price"] == pytest.approx(session.get(Market, order["market_id"]).yes_bid)
        proceeds += order["gross"] - order["fee"]

    account = sold["account"]
    assert account["baskets"] == []
    assert account["holdings_value"] == 0
    assert account["cash"] == pytest.approx(10_000 - cost + proceeds)
    realized = sum(o["realized_pnl"] for o in sold["basket_order"]["orders"])
    assert realized == pytest.approx(proceeds - cost)
    assert account["realized_pnl"] == pytest.approx(realized)
    # A round trip pays the spread and two fees.
    assert account["pnl"] == pytest.approx(realized) and realized < 0

    history = client.get(f"{PAPER}/orders").json()
    assert [(o["kind"], o["origin"]) for o in history] == [("sell", "manual"), ("buy", "manual")]


def test_partial_sell_rounds_down(client):
    basket = _basket(client)
    held = {
        h["market"]["id"]: h["contracts"]
        for h in _held(_buy(client, basket["id"], 1000).json()["account"], basket["id"])["holdings"]
    }
    orders = _sell(client, basket["id"], fraction=0.5).json()["basket_order"]["orders"]
    for order in orders:
        assert order["count"] == math.floor(held[order["market_id"]] * 0.5)


def test_sell_without_holdings_conflicts(client):
    basket = _basket(client)
    assert _sell(client, basket["id"]).status_code == 409


def test_sell_into_thin_bids_keeps_the_rest(app, client, session):
    basket = _basket(client, n=1)
    _buy(client, basket["id"], 500)
    market = session.get(Market, _ids(basket)[0])
    _fake(app).books[market.external_id] = OrderBook(yes_bids=((market.yes_bid, 7.0),), no_bids=())

    sold = _sell(client, basket["id"]).json()
    assert sold["basket_order"]["orders"][0]["filled_count"] == 7
    assert "not enough bids" in sold["skipped"][0]["reason"]
    assert len(_held(sold["account"], basket["id"])["holdings"]) == 1


def test_sell_leaves_closed_markets_held(client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    closed_id = _ids(basket)[0]
    session.get(Market, closed_id).is_open = False
    session.commit()

    sold = _sell(client, basket["id"]).json()
    assert sold["skipped"] == [{"market_id": closed_id, "side": "yes", "reason": "market is closed"}]
    remaining = _held(sold["account"], basket["id"])["holdings"]
    assert [h["market"]["id"] for h in remaining] == [closed_id]


def test_rebalance_trades_holdings_back_to_target_and_resets_the_index(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 2000)
    a, b = _ids(basket)

    _set_price(app, session, a, session.get(Market, a).yes_price / 2)
    drifted = _held(client.get(PAPER).json(), basket["id"])
    assert drifted["max_drift"] > 0.1

    preview = _rebalance(client, basket["id"], dry_run=True).json()
    assert [(o["market_id"], o["action"]) for o in preview["orders"]] == [(b, "sell"), (a, "buy")]

    result = _rebalance(client, basket["id"])
    assert result.status_code == 200, result.text
    assert _held(result.json()["account"], basket["id"])["max_drift"] < 0.01
    assert result.json()["basket_order"]["kind"] == "rebalance"
    # The index is back at its targets too, so later buys mirror the holdings.
    assert client.get(f"/baskets/{basket['id']}").json()["max_drift"] < 1e-6

    assert _rebalance(client, basket["id"], dry_run=True).status_code == 409


def test_rebalance_sells_holdings_the_basket_no_longer_targets(client, session):
    basket = _basket(client, n=3)
    _buy(client, basket["id"], 3000)
    dropped = _ids(basket)[2]
    position = session.query(BasketPosition).filter_by(basket_id=basket["id"], market_id=dropped).one()
    session.delete(position)
    for p in session.query(BasketPosition).filter_by(basket_id=basket["id"]):
        p.target_weight = 0.5
    session.commit()

    held = _held(client.get(PAPER).json(), basket["id"])
    assert next(h for h in held["holdings"] if h["market"]["id"] == dropped)["target_weight"] == 0

    preview = _rebalance(client, basket["id"], dry_run=True).json()
    sell = next(o for o in preview["orders"] if o["market_id"] == dropped)
    assert sell["action"] == "sell"
    after = _held(_rebalance(client, basket["id"]).json()["account"], basket["id"])
    assert dropped not in {h["market"]["id"] for h in after["holdings"]}


def test_rebalance_leaves_a_leg_that_cannot_trade(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 2000)
    a, b = _ids(basket)
    _set_price(app, session, a, session.get(Market, a).yes_price / 2)
    # B is overweight, but nobody is bidding for it.
    _fake(app).books[session.get(Market, b).external_id] = OrderBook(yes_bids=(), no_bids=((0.3, 1000.0),))

    preview = _rebalance(client, basket["id"], dry_run=True).json()
    assert {"market_id": b, "side": "yes", "reason": "no bids"} in preview["skipped"]


def test_resolved_markets_pay_out_on_sync(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    won, other = _ids(basket)
    before = _held(client.get(PAPER).json(), basket["id"])
    contracts = next(h["contracts"] for h in before["holdings"] if h["market"]["id"] == won)
    cash = client.get(PAPER).json()["cash"]
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]

    _fake(app).resolve(session.get(Market, won).external_id, 1.0)
    counts = client.post("/markets/sync").json()
    assert (counts["resolved"], counts["settled"]) == (1, 1)

    account = client.get(PAPER).json()
    assert account["cash"] == pytest.approx(cash + contracts)
    assert [h["market"]["id"] for h in _held(account, basket["id"])["holdings"]] == [other]
    payout = client.get(f"{PAPER}/orders").json()[0]
    assert (payout["kind"], payout["origin"]) == ("settle", "settlement")
    assert payout["orders"][0]["fee"] == 0 and payout["orders"][0]["realized_pnl"] > 0

    # The index holds the resolved leg at its payout, so the NAV doesn't jump...
    detail = client.get(f"/baskets/{basket['id']}").json()
    resolved = next(p for p in detail["positions"] if p["market"]["id"] == won)
    assert resolved["price"] == 1.0 and resolved["market"]["settlement_price"] == 1.0
    assert abs(detail["nav"] - nav) < 25
    # ...and rebalancing takes it out, putting all the weight on what's left.
    session.expire_all()
    _rebalance(client, basket["id"])
    detail = client.get(f"/baskets/{basket['id']}").json()
    assert [(p["market"]["id"], p["target_weight"]) for p in detail["positions"]] == [(other, 1.0)]


def test_a_losing_payout_realizes_the_whole_cost(app, client, session):
    basket = _basket(client, n=1)
    cost = _held(_buy(client, basket["id"], 500).json()["account"], basket["id"])["cost"]
    _fake(app).resolve(session.get(Market, _ids(basket)[0]).external_id, 0.0)
    client.post("/markets/sync")
    account = client.get(PAPER).json()
    assert account["baskets"] == []
    assert account["realized_pnl"] == pytest.approx(-cost)


def test_shares_track_the_index(app, client, session):
    basket = _basket(client)
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    held = _held(_buy(client, basket["id"], 1000).json()["account"], basket["id"])
    assert held["opened_nav"] == pytest.approx(nav)
    # Paying the spread and Kalshi's fee (about 2% at these prices) costs against the index on day one.
    assert held["share_value"] < nav
    assert -0.05 < held["holding_return"] < 0
    day_one = 1 + held["holding_return"]

    a, _ = _ids(basket)
    _set_price(app, session, a, session.get(Market, a).yes_price * 1.2)
    held = _held(client.get(PAPER).json(), basket["id"])
    assert held["index_return"] > 0.05
    # Holdings mirror the index, so after the first day's costs the holding moves with it.
    assert 1 + held["holding_return"] == pytest.approx((1 + held["index_return"]) * day_one, rel=0.005)

    # Adding money doesn't change the return: new shares are issued at the current share value.
    before = held["share_value"]
    after = _held(_buy(client, basket["id"], 1000).json()["account"], basket["id"])
    assert after["share_value"] == pytest.approx(before, rel=0.03)
    assert after["shares"] > held["shares"]

    points = client.get(f"{PAPER}/baskets/{basket['id']}/history").json()
    assert len(points) == 2
    assert points[-1]["share_value"] == pytest.approx(after["share_value"])


def test_a_trade_planned_on_a_stale_balance_does_not_commit(app, client, session):
    basket = _basket(client)
    account_id = client.get(PAPER).json() and session.query(Account).one().id
    broker = app.state.brokers[trading.AccountMode.PAPER]

    with app.state.sessionmaker() as first, app.state.sessionmaker() as second:
        slow = first.get(Account, account_id)
        plan = trading.plan_buy(slow, first.get(trading.Basket, basket["id"]), 500, broker)

        fast = second.get(Account, account_id)
        trading.execute(second, fast, second.get(trading.Basket, basket["id"]), trading.plan_buy(
            fast, second.get(trading.Basket, basket["id"]), 500, broker
        ), broker)
        second.commit()

        with pytest.raises(StaleDataError):
            trading.execute(first, slow, first.get(trading.Basket, basket["id"]), plan, broker)
            first.commit()
        first.rollback()

    assert len(client.get(f"{PAPER}/orders").json()) == 1


def test_fills_use_up_cached_liquidity(app, session):
    market = session.query(Market).first()
    broker = PaperBroker(FEE_RATE, _fake(app), cache_seconds=60)
    book = broker.books([(market, Side.YES, Action.BUY)])[0]
    plan = trading._planned(market, Side.YES, book, book.take(400), broker)
    assert broker.execute(plan.request).count == 400
    # 100 are left at the top level, so the next 400 walk one level further.
    again = broker.books([(market, Side.YES, Action.BUY)])[0]
    assert again.levels[0] == (pytest.approx(book.best), pytest.approx(100.0))


def test_auto_rebalance_runs_on_sync(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 2000)
    resp = client.put(f"{PAPER}/baskets/{basket['id']}/automation", json={"auto_rebalance_drift": 0.05})
    assert resp.json()["auto_rebalance_drift"] == 0.05

    a, _ = _ids(basket)
    _set_price(app, session, a, session.get(Market, a).yes_price / 2)
    client.post("/markets/sync")

    latest = client.get(f"{PAPER}/orders").json()[0]
    assert (latest["kind"], latest["origin"]) == ("rebalance", "auto_rebalance")
    assert _held(client.get(PAPER).json(), basket["id"])["max_drift"] < 0.05
    assert "Rebalanced automatically" in client.get(f"{PAPER}/baskets/{basket['id']}/automation").json()["note"]


def test_recurring_buys_run_when_due(client):
    basket = _basket(client)
    start = (utcnow() - timedelta(minutes=1)).isoformat()
    body = {"recurring_amount": 250, "recurring_days": 7, "recurring_start": start}
    assert client.put(f"{PAPER}/baskets/{basket['id']}/automation", json=body).status_code == 200

    assert client.post("/markets/sync").json()["automated"] == 1
    orders = client.get(f"{PAPER}/orders").json()
    assert [(o["kind"], o["origin"], o["amount"]) for o in orders] == [("buy", "recurring", 250)]
    automation = client.get(f"{PAPER}/baskets/{basket['id']}/automation").json()
    # The next one is a week out, so another sync now buys nothing.
    assert automation["next_recurring_at"] > utcnow().isoformat()
    assert client.post("/markets/sync").json()["automated"] == 0

    off = client.put(f"{PAPER}/baskets/{basket['id']}/automation", json={}).json()
    assert off["recurring_amount"] is None and off["next_recurring_at"] is None


def test_recurring_needs_amount_and_days(client):
    basket = _basket(client)
    assert client.put(f"{PAPER}/baskets/{basket['id']}/automation", json={"recurring_amount": 50}).status_code == 422


def test_stop_loss_sells_when_the_nav_falls(app, client, session):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    body = {"action": "sell", "amount": 1, "condition": "at_or_below", "nav": nav * 0.9}
    order = client.post(f"{PAPER}/baskets/{basket['id']}/conditional-orders", json=body).json()
    assert order["status"] == "open"

    client.post("/markets/sync")
    assert client.get(f"{PAPER}/conditional-orders").json()[0]["status"] == "open"

    for market_id in _ids(basket):
        _set_price(app, session, market_id, session.get(Market, market_id).yes_price * 0.7)
    client.post("/markets/sync")

    fired = client.get(f"{PAPER}/conditional-orders").json()[0]
    assert fired["status"] == "filled" and fired["basket_order_id"]
    assert client.get(PAPER).json()["baskets"] == []
    assert client.get(f"{PAPER}/orders").json()[0]["origin"] == "conditional"


def test_a_conditional_order_that_cannot_fill_fails_with_a_note(app, client, session):
    basket = _basket(client)
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    body = {"action": "buy", "amount": 50_000, "condition": "at_or_above", "nav": nav * 1.01}
    client.post(f"{PAPER}/baskets/{basket['id']}/conditional-orders", json=body)
    for market_id in _ids(basket):
        _set_price(app, session, market_id, session.get(Market, market_id).yes_price * 1.3)
    client.post("/markets/sync")
    order = client.get(f"{PAPER}/conditional-orders").json()[0]
    assert order["status"] == "failed" and "Not enough cash" in order["note"]


def test_conditional_orders_are_validated_and_can_be_cancelled(client):
    basket = _basket(client)
    url = f"{PAPER}/baskets/{basket['id']}/conditional-orders"
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    # Already true: place a regular order instead.
    already = client.post(url, json={"action": "buy", "amount": 100, "condition": "at_or_below", "nav": nav + 1})
    assert already.status_code == 409
    assert client.post(url, json={"action": "sell", "amount": 2, "condition": "at_or_below", "nav": 50}).status_code == 422

    order = client.post(url, json={"action": "buy", "amount": 100, "condition": "at_or_below", "nav": nav - 5}).json()
    assert client.delete(f"{PAPER}/conditional-orders/{order['id']}").status_code == 204
    assert client.delete(f"{PAPER}/conditional-orders/{order['id']}").status_code == 409
    assert client.get(f"{PAPER}/conditional-orders", params={"status": "open"}).json() == []


def test_basket_with_holdings_cannot_be_deleted_until_sold(client):
    basket = _basket(client)
    _buy(client, basket["id"], 100)
    assert client.delete(f"/baskets/{basket['id']}").status_code == 409

    _sell(client, basket["id"])
    assert client.delete(f"/baskets/{basket['id']}").status_code == 204
    # Order history outlives the basket.
    history = client.get(f"{PAPER}/orders").json()
    assert {(o["basket_id"], o["basket_name"]) for o in history} == {(None, "Trading basket")}


def test_reset_restores_starting_cash_and_clears_everything(client):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    client.put(f"{PAPER}/baskets/{basket['id']}/automation", json={"auto_rebalance_drift": 0.1})
    nav = client.get(f"/baskets/{basket['id']}").json()["nav"]
    client.post(
        f"{PAPER}/baskets/{basket['id']}/conditional-orders",
        json={"action": "sell", "amount": 1, "condition": "at_or_below", "nav": nav / 2},
    )

    account = client.post(f"{PAPER}/reset").json()
    assert account["cash"] == 10_000
    assert account["baskets"] == []
    assert account["realized_pnl"] == 0
    assert account["reset_at"] is not None
    assert client.get(f"{PAPER}/orders").json() == []
    assert client.get(f"{PAPER}/conditional-orders").json() == []
    assert client.get(f"{PAPER}/baskets/{basket['id']}/automation").json()["auto_rebalance_drift"] is None
    assert len(client.get(f"{PAPER}/equity").json()) == 1


def test_sync_records_account_equity_and_stake_values(client):
    basket = _basket(client)
    _buy(client, basket["id"], 1000)
    assert client.post("/markets/sync").status_code == 200
    # Opening, the buy, and the sync.
    points = client.get(f"{PAPER}/equity").json()
    assert len(points) == 3
    assert points[-1]["equity"] == pytest.approx(client.get(PAPER).json()["equity"])
    assert len(client.get(f"{PAPER}/baskets/{basket['id']}/history").json()) == 2
