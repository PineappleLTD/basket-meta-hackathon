from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.brokers import PaperBroker
from app.kalshi import OrderBook
from app.models import Account, AccountMode, Basket, Holding, Market, Side, utcnow
from app.news_models import NewsCash, NewsContract, NewsCycle, NewsPolicy, NewsSignal
from app.services import news, news_execution, trading
from tests.conftest import signup


class Books:
    def fetch_orderbooks(self, tickers):
        return {ticker: OrderBook(((0.5, 10000),), ((0.5, 10000),)) for ticker in tickers}


class PartialPaper(PaperBroker):
    def __init__(self, first_sale=40):
        super().__init__(0, Books(), cache_seconds=0)
        self.first_sale = first_sale
        self.requests = []

    def execute(self, order):
        self.requests.append(order)
        if order.action.value == "sell" and self.first_sale is not None:
            count, self.first_sale = self.first_sale, None
            return super().execute(replace(order, count=min(order.count, count)))
        return super().execute(order)


@pytest.fixture
def portfolio(client, session, app):
    markets = session.scalars(select(Market).limit(3)).all()
    basket_data = client.post("/baskets", json={"name": "News ETF", "positions": [
        {"market_id": m.id, "side": "yes"} for m in markets]}).json()
    client.get("/accounts/paper")
    session.expire_all()
    account = session.scalar(select(Account))
    basket = session.get(Basket, basket_data["id"])
    for m in markets:
        m.yes_price, m.yes_bid, m.yes_ask = 0.5, 0.5, 0.5
        m.closes_at = utcnow() + timedelta(days=30)
        account.holdings.append(Holding(basket=basket, market=m, side=Side.YES, contracts=200, cost_cents=10000))
        session.add(NewsContract(market_id=m.id, rules=f"Exact rule for {m.external_id}", deadline=m.closes_at))
    session.add(NewsPolicy(basket_id=basket.id, mode="suggest", sensitivity=0.5, max_shift=1))
    session.flush()
    topic = news.watchlist(session, basket)["topics"][0]
    contract = topic["contract"]
    sig = NewsSignal(id="a" * 64, basket_id=basket.id, market_id=topic["market_id"], side="yes",
                     published_at=utcnow(), payload={"article": article(), "contract_hash": news.digest(contract),
                        "result": {"classification": "ADVERSE", "confidence": 0.95, "experimental": True}})
    session.add(sig)
    session.commit()
    broker = PartialPaper()
    app.state.brokers[AccountMode.PAPER] = broker
    return account, basket, broker


def article(**changes):
    return {"headline": "A verified setback to the condition", "summary": "Source reports a setback.",
            "published_at": utcnow().isoformat(), "source": "Test source", "url": "https://example.com/story", **changes}


def start(session, portfolio):
    account, basket, broker = portfolio
    plan = news.proposal(session, account, basket)
    cycle = news_execution.start(session, account, basket, broker, plan["id"])
    session.commit()
    return cycle


def step(session, portfolio, cycle, **kwargs):
    account, basket, broker = portfolio
    news_execution.advance(session, account, basket, broker, cycle, **kwargs)
    session.commit()
    return cycle


def test_100_share_sale_waits_after_40_confirmed_then_60(portfolio, session):
    cycle = start(session, portfolio)
    assert cycle.payload["sells"][0]["count"] == 100
    step(session, portfolio, cycle)
    assert cycle.status == "SELLING"
    assert cycle.payload["sells"][0]["filled"] == 40
    assert all(order.action.value == "sell" for order in portfolio[2].requests)
    assert news.basket_cash(session, portfolio[0].id, portfolio[1].id) == 2000
    step(session, portfolio, cycle)
    assert cycle.status == "BUYING"
    assert cycle.payload["sells"][0]["filled"] == 100
    assert all(order.action.value == "sell" for order in portfolio[2].requests)
    step(session, portfolio, cycle)
    assert cycle.status == "COMPLETE"
    buys = [o for o in portfolio[2].requests if o.action.value == "buy"]
    assert len(buys) == 2
    assert {o.market.id for o in buys} <= {p.market_id for p in portfolio[1].positions}
    assert all(o.side == Side.YES for o in buys)
    assert cycle.payload["spent_cents"] <= cycle.payload["proceeds_cents"]


def test_restart_preserves_original_sale_and_no_duplicate_fills(portfolio, session, app):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    account_id, basket_id, cycle_id = portfolio[0].id, portfolio[1].id, cycle.id
    with app.state.sessionmaker() as restored:
        account, basket = restored.get(Account, account_id), restored.get(Basket, basket_id)
        recovered = restored.get(NewsCycle, cycle_id)
        news_execution.advance(restored, account, basket, portfolio[2], recovered)
        restored.commit()
        assert recovered.status == "BUYING"
        assert recovered.payload["sells"][0]["filled"] == 100
    assert [o.count for o in portfolio[2].requests] == [100, 60]


def test_repeated_start_and_signal_do_not_create_another_cycle(portfolio, session):
    cycle = start(session, portfolio)
    assert news_execution.start(session, *portfolio).id == cycle.id
    for _ in range(3):
        step(session, portfolio, cycle)
    calls = len(portfolio[2].requests)
    step(session, portfolio, cycle)
    assert len(portfolio[2].requests) == calls
    with pytest.raises(news.NewsError, match="No new accepted"):
        news_execution.start(session, *portfolio)
    session.rollback()


@pytest.mark.parametrize("change", ["side", "outside", "holdings"])
def test_scope_or_external_holding_change_blocks_before_purchases(change, portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    account, basket, broker = portfolio
    if change == "side":
        basket.positions[1].side = Side.NO
    elif change == "outside":
        basket.positions.pop()
    else:
        account.holdings[1].contracts += 1
    session.commit()
    step(session, portfolio, cycle)
    assert cycle.status == "BLOCKED"
    assert not any(o.action.value == "buy" for o in broker.requests)


def test_expiration_and_turning_off_preserve_partial_sale_cash(portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle, now=news.utc(cycle.expires_at) + timedelta(seconds=1))
    assert cycle.status == "EXPIRED"
    assert news.basket_cash(session, portfolio[0].id, portfolio[1].id) == 2000


def test_cancel_mode_stops_pending_work(portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    session.get(NewsPolicy, portfolio[1].id).mode = "off"
    session.commit()
    step(session, portfolio, cycle)
    assert cycle.status == "CANCELLED"
    assert len(portfolio[2].requests) == 1


def test_no_known_depth_waits_instead_of_using_unlimited_fallback(portfolio, session):
    cycle = start(session, portfolio)
    portfolio[2].source = None
    portfolio[2]._cache.clear()
    step(session, portfolio, cycle)
    assert cycle.status == "SELLING"
    assert cycle.payload["sells"][0]["filled"] == 0
    assert portfolio[2].requests == []


def test_other_trades_cannot_spend_reserved_proceeds(portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    account, basket, broker = portfolio
    assert trading.spendable_cash(account) == account.cash_cents - 2000
    with pytest.raises(trading.TradingError, match="reserved"):
        trading.plan_buy(account, basket, account.cash, broker)
    with pytest.raises(trading.TradingError, match="news rebalance"):
        trading.execute(session, account, basket, trading.plan_sell(account, basket, 0.1, broker), broker)


def test_stale_proposal_and_live_broker_are_rejected(portfolio, session):
    account, basket, broker = portfolio
    with pytest.raises(news.NewsError, match="refresh"):
        news_execution.start(session, account, basket, broker, "stale")
    session.rollback()
    with pytest.raises(news.NewsError, match="paper"):
        news_execution.start(session, account, basket, object())


def test_owner_scope_and_client_cannot_inject_scores(client, portfolio):
    basket_id = portfolio[1].id
    prefix = f"/baskets/{basket_id}/climate"
    assert client.post(f"{prefix}/articles", json={**article(), "classification": "ADVERSE"}).status_code == 422
    other = signup(client, "other@example.com")
    assert client.get(prefix, headers=other).status_code == 404
    assert client.put(f"{prefix}/settings", headers=other,
                      json={"mode": "auto", "sensitivity": 0.5, "max_shift": 0.05}).status_code == 404


def test_settings_and_api_apply_advance_flow(client, portfolio):
    prefix = f"/baskets/{portfolio[1].id}/climate"
    before = client.get(prefix)
    assert before.status_code == 200, before.text
    assert before.json()["execution_mode"] == "paper"
    pid = before.json()["proposal"]["id"]
    result = client.post(f"{prefix}/apply", json={"expected_id": pid})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "SELLING"
    assert result.json()["sells"][0]["filled"] == 40
    assert client.post(f"{prefix}/advance").json()["status"] == "BUYING"
    assert client.post(f"{prefix}/advance").json()["status"] == "COMPLETE"


def test_no_eligible_recipient_keeps_all_proceeds_in_basket_cash(portfolio, session):
    account, basket, broker = portfolio
    for p in basket.positions[1:]:
        p.market.is_open = False
    session.commit()
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    assert cycle.status == "COMPLETE"
    assert news.basket_cash(session, account.id, basket.id) == 5000
    assert not any(o.action.value == "buy" for o in broker.requests)


def test_auto_scheduler_continues_saved_cycle(portfolio, session, app):
    session.get(NewsPolicy, portfolio[1].id).mode = "auto"
    session.commit()
    for _ in range(3):
        news_execution.run(session, app.state.brokers)
    session.expire_all()
    cycle = session.scalar(select(NewsCycle))
    assert cycle.status == "COMPLETE"
    assert len(session.scalars(select(NewsCycle)).all()) == 1


def test_reset_clears_cash_reservations_and_pending_cycles(client, portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    assert client.post("/accounts/paper/reset").status_code == 200
    session.expire_all()
    assert session.scalar(select(NewsCash)) is None
    assert session.scalar(select(NewsCycle)) is None


def adverse_for_every_position(session, portfolio):
    account, basket, broker = portfolio
    for i, topic in enumerate(news.watchlist(session, basket)["topics"][1:], 1):
        session.add(NewsSignal(id=str(i) * 64, basket_id=basket.id, market_id=topic["market_id"], side="yes",
                              published_at=utcnow(), payload={"article": article(headline=f"Setback {i}"),
                                  "contract_hash": news.digest(topic["contract"]),
                                  "result": {"classification": "ADVERSE", "confidence": 0.95}}))
    session.commit()


def test_completed_allocation_is_saved_but_partial_sale_does_not_change_targets(portfolio, session):
    account, basket, broker = portfolio
    before = {p.market_id: p.target_weight for p in basket.positions}
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    assert {p.market_id: p.target_weight for p in basket.positions} == before
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    saved = {p.market_id: p.target_weight for p in basket.positions}
    seller = cycle.payload["sells"][0]["market_id"]
    assert saved[seller] == pytest.approx(1 / 6)
    assert sum(saved.values()) == pytest.approx(1)
    assert saved != before


def test_cash_counts_in_etf_value_without_counting_twice_in_account(portfolio, session):
    adverse_for_every_position(session, portfolio)
    account, basket, broker = portfolio
    before = trading.value_account(account).equity
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    assert cycle.status == "COMPLETE"
    from app.services.baskets import value_basket
    holding = trading.value_basket_holdings(account, basket)
    assert holding.value == 300
    assert holding.cash == 150
    assert trading.value_account(account).equity == before
    assert value_basket(basket).cash_target_weight == pytest.approx(0.5)
    assert sum(p.target_weight for p in basket.positions) == pytest.approx(0.5)
    assert holding.share_value == pytest.approx(holding.value / holding.stake.shares)


def test_future_deposit_obeys_saved_cash_and_position_allocation(portfolio, session):
    adverse_for_every_position(session, portfolio)
    account, basket, broker = portfolio
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    plan = trading.plan_buy(account, basket, 100, broker)
    assert plan.reserve_cash_cents == 5000
    assert all(o.request.action.value == "buy" for o in plan.orders)
    assert -plan.cash_delta_cents + plan.reserve_cash_cents <= 10000
    trading.execute(session, account, basket, plan, broker)
    session.commit()
    assert news.basket_cash(session, account.id, basket.id) == 20000


def test_all_cash_etf_supports_deposit_and_withdrawal_without_new_positions(portfolio, session):
    adverse_for_every_position(session, portfolio)
    account, basket, broker = portfolio
    session.get(NewsPolicy, basket.id).sensitivity = 1
    session.commit()
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    assert cycle.status == "COMPLETE"
    assert not [h for h in account.holdings if h.basket_id == basket.id]
    assert news.basket_cash(session, account.id, basket.id) == 30000
    deposit = trading.plan_buy(account, basket, 100, broker)
    assert deposit.orders == []
    trading.execute(session, account, basket, deposit, broker)
    session.commit()
    assert news.basket_cash(session, account.id, basket.id) == 40000
    assert trading.value_basket_holdings(account, basket).value == 400
    withdrawal = trading.plan_sell(account, basket, 1, broker)
    trading.execute(session, account, basket, withdrawal, broker)
    session.commit()
    assert news.basket_cash(session, account.id, basket.id) == 0
    assert account.holdings == []


def test_republished_story_cannot_trigger_another_reduction(portfolio, session):
    cycle = start(session, portfolio)
    for _ in range(3):
        step(session, portfolio, cycle)
    original = session.get(NewsSignal, "a" * 64)
    session.add(NewsSignal(id="z" * 64, basket_id=original.basket_id, market_id=original.market_id, side=original.side,
                          published_at=utcnow(), payload={**original.payload,
                              "article": {**original.payload["article"], "url": "https://other.example.com/reprint"}}))
    session.commit()
    assert news.proposal(session, portfolio[0], portfolio[1])["reason"] == "No new accepted adverse evidence"


def test_changed_contract_rules_invalidate_pending_plan(portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    spec = session.get(NewsContract, cycle.payload["sells"][0]["market_id"])
    spec.rules = "Amended resolution condition"
    session.commit()
    step(session, portfolio, cycle)
    assert cycle.status == "BLOCKED"
    assert not any(o.action.value == "buy" for o in portfolio[2].requests)


def test_new_adverse_news_on_recipient_stops_purchase(portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle)
    recipient = cycle.payload["buys"][0]
    topic = next(t for t in news.watchlist(session, portfolio[1])["topics"] if t["market_id"] == recipient["market_id"])
    session.add(NewsSignal(id="new-adverse", basket_id=portfolio[1].id, market_id=recipient["market_id"], side="yes",
                          published_at=utcnow(), payload={"article": article(headline="New setback to recipient"),
                              "contract_hash": news.digest(topic["contract"]),
                              "result": {"classification": "ADVERSE", "confidence": 0.99}}))
    session.commit()
    step(session, portfolio, cycle)
    assert cycle.status == "BLOCKED"
    assert not any(o.action.value == "buy" for o in portfolio[2].requests)


def test_invalid_broker_fill_blocks_automatic_cycle_instead_of_retrying(portfolio, session, app):
    from app.brokers import Fill
    class InvalidPaper(PartialPaper):
        def execute(self, request):
            return Fill(request.count + 1, 0.5, 100, 0)
    app.state.brokers[AccountMode.PAPER] = InvalidPaper()
    session.get(NewsPolicy, portfolio[1].id).mode = "auto"
    session.commit()
    # Persist a cycle first, then introduce the failing broker.
    cycle = start(session, portfolio)
    before = portfolio[0].cash_cents
    news_execution.run(session, app.state.brokers)
    session.expire_all()
    assert session.get(NewsCycle, cycle.id).status == "BLOCKED"
    assert portfolio[0].cash_cents == before


def test_first_automatic_failure_keeps_a_durable_blocked_goal(portfolio, session, app):
    from app.brokers import Fill
    class InvalidPaper(PartialPaper):
        def execute(self, request):
            return Fill(request.count + 1, 0.5, 100, 0)
    app.state.brokers[AccountMode.PAPER] = InvalidPaper()
    session.get(NewsPolicy, portfolio[1].id).mode = "auto"
    session.commit()
    news_execution.run(session, app.state.brokers)
    session.expire_all()
    cycle = session.scalar(select(NewsCycle))
    assert cycle.status == "BLOCKED"
    assert cycle.payload["sells"][0]["count"] == 100
    assert cycle.payload["sells"][0]["filled"] == 0


def test_cash_movement_has_a_matching_persistent_order_record(client, portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    result = client.get(f"/accounts/paper/orders?basket_id={portfolio[1].id}").json()
    assert len(result) == 1
    assert result[0]["basket_cash_delta"] == 20
    assert result[0]["orders"][0]["filled_count"] == 40


@pytest.mark.parametrize("fee_rate", [0.0, 0.07])
@pytest.mark.parametrize("spread", [0.0, 0.02])
@pytest.mark.parametrize("side", [Side.YES, Side.NO])
def test_accounting_conserves_cash_and_positions_with_fees_spreads_and_both_sides(fee_rate, spread, side, portfolio, session):
    from app.models import BasketOrder, NewsCashMovement
    account, basket, broker = portfolio
    if side == Side.NO:
        for h in account.holdings:
            h.side = Side.NO
        for p in basket.positions:
            p.side = Side.NO
        signal = session.get(NewsSignal, "a" * 64)
        signal.side = "no"
        session.flush()
        spec = next(t for t in news.watchlist(session, basket)["topics"] if t["market_id"] == signal.market_id)
        signal.payload = {**signal.payload, "contract_hash": news.digest(spec["contract"])}
        session.commit()
    class SpreadBooks:
        def fetch_orderbooks(self, tickers):
            return {t: OrderBook(((0.5 - spread, 10000),), ((0.5 - spread, 10000),)) for t in tickers}
    broker.source, broker.fee_rate = SpreadBooks(), fee_rate
    before = trading.value_account(account).equity
    cycle = start(session, portfolio)
    for _ in range(5):
        step(session, portfolio, cycle)
        if cycle.status == "COMPLETE":
            break
    assert cycle.status == "COMPLETE"
    orders = [o for record in session.scalars(select(BasketOrder)).all() for o in record.orders]
    expected_cost = sum(o.fee_cents / 100 + o.filled_count *
                        ((o.fill_price - 0.5) if o.action.value == "buy" else (0.5 - o.fill_price)) for o in orders)
    assert trading.value_account(account).equity == pytest.approx(before - expected_cost)
    assert account.cash_cents >= news.reserved_cash(session, account.id) >= 0
    assert news.basket_cash(session, account.id, basket.id) == sum(session.scalars(select(NewsCashMovement.delta_cents)))
    assert cycle.payload["spent_cents"] <= cycle.payload["proceeds_cents"]
    assert all(o.side == side for o in orders)


def test_expired_partial_sale_cannot_be_undone_by_other_automation(portfolio, session, app):
    from app.services import automation
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle, now=news.utc(cycle.expires_at) + timedelta(seconds=1))
    account, basket, broker = portfolio
    trading.find_stake(account, basket.id).auto_rebalance_drift = 0.01
    session.get(NewsPolicy, basket.id).mode = "auto"
    session.commit()
    requests = len(broker.requests)
    automation.run(session, app.state.brokers, news.utc(cycle.expires_at) + timedelta(seconds=2))
    assert len(broker.requests) == requests
    with pytest.raises(news.NewsError, match="Review"):
        news_execution.start(session, *portfolio)
    session.rollback()


def test_owner_can_review_stopped_cycle_and_keep_current_allocation(client, portfolio, session):
    cycle = start(session, portfolio)
    step(session, portfolio, cycle)
    step(session, portfolio, cycle, now=news.utc(cycle.expires_at) + timedelta(seconds=1))
    prefix = f"/baskets/{portfolio[1].id}/climate"
    response = client.post(prefix + "/review", json={"cycle_id": cycle.id})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REVIEWED"
    session.expire_all()
    assert session.get(NewsPolicy, portfolio[1].id).mode == "off"
    assert news.pending_review(session, portfolio[0].id, portfolio[1].id) is None
    assert news.basket_cash(session, portfolio[0].id, portfolio[1].id) == 2000
    assert client.get(f"/baskets/{portfolio[1].id}").json()["cash_target_weight"] > 0
    assert client.post(prefix + "/review", json={"cycle_id": cycle.id}).status_code == 409
