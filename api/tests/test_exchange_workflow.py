from decimal import Decimal
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.kalshi.execution import ExecutionFill, Snapshot, SubmissionUnknown, OrderRejected, VenueError
from app.models import Account, AccountMode, Action, Basket, Side
from app.news_models import ExchangeOrder, ExchangeFill, LiveControl, NewsCycle, NewsPolicy
from app.services import exchange_execution as execution, news
from tests.test_news import portfolio


class SimulatedVenue:
    environment, subaccount = "demo", 0

    def __init__(self, account):
        self.cash = account.cash_cents
        self.positions = execution.expected_positions(account)
        self.orders, self.posts, self.cancels = {}, [], []
        self.first_sale = True
        self.timeout_after_accept = False
        self.reject = False
        self.rest_first = False
        self.missing_fills = False

    def account_snapshot(self):
        return {"cash_cents": self.cash, "positions": self.positions.copy()}

    def _fill(self, intent, count, index):
        direction = 1 if (intent.side == Side.YES) == (intent.action == Action.BUY) else -1
        self.positions[intent.ticker] = self.positions.get(intent.ticker, 0) + direction * count
        if self.positions[intent.ticker] == 0:
            self.positions.pop(intent.ticker)
        self.cash += count * 50 * (1 if intent.action == Action.SELL else -1)
        return ExecutionFill(f"{intent.client_id}-{index}", count, Decimal("0.5"), count * 50, 0)

    def submit(self, intent):
        self.posts.append(intent)
        if self.reject:
            raise OrderRejected("Synthetic rejection")
        assert intent.client_id not in self.orders, "A second POST attempted to submit the same intent"
        count = min(40, intent.count) if self.first_sale and intent.action == Action.SELL else intent.count
        resting = self.first_sale and self.rest_first and intent.action == Action.SELL
        self.first_sale = False
        fill = self._fill(intent, count, 1)
        self.orders[intent.client_id] = (intent, Snapshot(intent.client_id, "resting" if resting else "canceled",
                                    count, intent.count - count if resting else 0, (fill,)))
        if self.timeout_after_accept:
            self.timeout_after_accept = False
            raise SubmissionUnknown("Synthetic timeout after exchange acceptance")
        return intent.client_id

    def reconcile(self, intent, order_id=None):
        if intent.client_id not in self.orders or self.missing_fills:
            raise SubmissionUnknown("Not yet visible")
        return self.orders[intent.client_id][1]

    def finish_resting(self, client_id):
        intent, old = self.orders[client_id]
        extra = self._fill(intent, intent.count - old.filled_count, 2)
        self.orders[client_id] = (intent, Snapshot(client_id, "executed", intent.count, 0, old.fills + (extra,)))

    def cancel(self, order_id):
        self.cancels.append(order_id)
        intent, old = self.orders[order_id]
        self.orders[order_id] = (intent, Snapshot(order_id, "canceled", old.filled_count, 0, old.fills))


@pytest.fixture
def external(portfolio, session, app):
    account, basket, quotes = portfolio
    account.mode = AccountMode.LIVE
    session.commit()
    venue = SimulatedVenue(account)
    gateway = execution.Gateway(venue, quotes, account.user_id, "synthetic-demo-scope", 100000, 1000000, 0, 1000000)
    app.state.exchange_gateway = gateway
    session.add(LiveControl(account_id=account.id, basket_id=basket.id, armed=False, scope=gateway.scope))
    session.commit()
    return account, basket, gateway, app.state.sessionmaker


def begin(external, session):
    account, basket, gateway, factory = external
    pid = news.proposal(session, account, basket)["id"]
    session.commit()
    return execution.start_cycle(factory, gateway, account.id, basket.id, pid)


def tick(external, cid, **kwargs):
    account, basket, gateway, factory = external
    execution.advance(factory, gateway, cid, **kwargs)
    with factory() as s:
        c = s.get(NewsCycle, cid)
        return c.status, c.payload


def finish(external, cid):
    for _ in range(25):
        status, data = tick(external, cid)
        if status == "COMPLETE":
            return data
    raise AssertionError(f"Cycle did not finish: {status}")


def test_external_40_then_60_sale_barrier_and_saved_allocation(external, session):
    cid = begin(external, session)
    tick(external, cid)  # durable intent then external acceptance
    assert len(external[2].venue.posts) == 1
    assert all(i.action == Action.SELL for i in external[2].venue.posts)
    tick(external, cid)  # reconcile 40 and prepare/submit remaining 60
    with external[3]() as s:
        data = s.get(NewsCycle, cid).payload
        assert data["sells"][0]["filled"] == 40
        assert data["proceeds_cents"] == 2000 and data["spent_cents"] == 0
    assert [i.count for i in external[2].venue.posts] == [100, 60]
    status, data = tick(external, cid)
    assert status == "BUYING" and data["sells"][0]["filled"] == 100
    assert not any(i.action == Action.BUY for i in external[2].venue.posts)
    data = finish(external, cid)
    assert data["spent_cents"] <= data["proceeds_cents"]
    with external[3]() as s:
        account, basket = s.get(Account, external[0].id), s.get(Basket, external[1].id)
        assert execution.expected_positions(account) == external[2].venue.positions
        assert account.cash_cents == external[2].venue.cash
        assert sum(p.target_weight for p in basket.positions) == pytest.approx(1)


def test_scheduler_automates_only_when_armed_and_consumes_story_once(external, session):
    account, basket, gateway, factory = external
    assert execution.run(factory, gateway) == 0 and gateway.venue.posts == []
    session.get(LiveControl, account.id).armed = True
    session.commit()
    for _ in range(20):
        execution.run(factory, gateway)
        with factory() as s:
            cycle = s.scalar(select(NewsCycle).where(NewsCycle.account_id == account.id))
            if cycle and cycle.status == "COMPLETE":
                break
    assert cycle.status == "COMPLETE"
    assert sum(i.count for i in gateway.venue.posts if i.action == Action.BUY) == 100
    submissions = len(gateway.venue.posts)
    for _ in range(3):
        execution.run(factory, gateway)
    assert len(gateway.venue.posts) == submissions
    with factory() as s:
        assert len(s.scalars(select(NewsCycle)).all()) == 1


def test_lost_submission_response_recovers_by_stable_id_without_second_post(external, session):
    external[2].venue.timeout_after_accept = True
    cid = begin(external, session)
    tick(external, cid)
    with external[3]() as s:
        first = s.scalar(select(ExchangeOrder))
        assert first.status == "UNKNOWN"
        oid = first.id
    execution.dispatch(external[3], external[2], oid)
    assert len(external[2].venue.posts) == 1
    data = finish(external, cid)
    assert data["sells"][0]["filled"] == 100
    assert sum(i.client_id == oid for i in external[2].venue.posts) == 1


def test_repeated_fill_notifications_and_restart_do_not_double_book(external, session):
    cid = begin(external, session)
    tick(external, cid)
    with external[3]() as s:
        oid = s.scalar(select(ExchangeOrder.id))
    execution.reconcile(external[3], external[2], oid)
    with external[3]() as s:
        before = s.get(Account, external[0].id).cash_cents
    for _ in range(3):
        execution.reconcile(external[3], external[2], oid)
    with external[3]() as s:
        assert s.get(Account, external[0].id).cash_cents == before
        assert len(s.scalars(select(ExchangeFill)).all()) == 1


def test_a_resting_partial_order_must_finish_before_any_new_order(external, session):
    external[2].venue.rest_first = True
    cid = begin(external, session)
    tick(external, cid)
    for _ in range(3):
        status, data = tick(external, cid)
        assert status == "SELLING" and data["sells"][0]["filled"] == 40
    assert len(external[2].venue.posts) == 1
    external[2].venue.finish_resting(external[2].venue.posts[0].client_id)
    tick(external, cid)
    finish(external, cid)


def test_expiry_cancels_residual_reconciles_then_preserves_cash(external, session):
    external[2].venue.rest_first = True
    cid = begin(external, session)
    tick(external, cid)
    tick(external, cid)
    with external[3]() as s:
        expiry = news.utc(s.get(NewsCycle, cid).expires_at) + timedelta(seconds=1)
    status, data = tick(external, cid, now=expiry)
    assert len(external[2].venue.cancels) == 1
    assert status == "SELLING"  # cancel acknowledgement alone cannot unlock purchases/review
    status, data = tick(external, cid, now=expiry)
    assert status == "EXPIRED" and data["sells"][0]["filled"] == 40
    assert data["spent_cents"] == 0
    assert all(i.action == Action.SELL for i in external[2].venue.posts)


def test_rejection_stops_cycle_and_never_funds_purchases(external, session):
    external[2].venue.reject = True
    cid = begin(external, session)
    tick(external, cid)
    status, data = tick(external, cid)
    assert status == "BLOCKED" and data["proceeds_cents"] == 0
    assert len(external[2].venue.posts) == 1


def test_outside_account_change_blocks_before_submission(external, session):
    cid = begin(external, session)
    external[2].venue.cash += 100
    status, _ = tick(external, cid)
    assert status == "BLOCKED" and external[2].venue.posts == []


def test_scope_and_daily_limits_are_independent_of_model(external, session):
    cid = begin(external, session)
    external[2].max_daily_turnover_cents = 1
    status, _ = tick(external, cid)
    assert status == "BLOCKED" and external[2].venue.posts == []
    external[2].scope = "different-account"
    with pytest.raises(news.NewsError, match="scope"):
        tick(external, cid)


def test_disabled_runtime_and_owner_scoped_external_routes(client, external):
    account, basket, gateway, _ = external
    prefix = f"/exchange/baskets/{basket.id}"
    assert client.get("/exchange/status").json()["enabled_for_user"] is True
    from tests.conftest import signup
    other = signup(client, "other-exchange@example.com")
    assert client.get(prefix, headers=other).status_code == 404
    assert client.get(prefix).status_code == 200


def test_external_api_start_and_step_uses_same_durable_workflow(client, external):
    prefix = f"/exchange/baskets/{external[1].id}"
    state = client.get(prefix).json()
    result = client.post(prefix + "/apply", json={"expected_id": state["proposal"]["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["orders"][0]["status"] == "SUBMITTED"
    for _ in range(20):
        result = client.post(prefix + "/advance")
        assert result.status_code == 200, result.text
        if result.json()["cycle"]["status"] == "COMPLETE":
            break
    assert result.json()["cycle"]["status"] == "COMPLETE"


def test_lagging_individual_fills_block_replacement_submissions(external, session):
    cid = begin(external, session)
    tick(external, cid)
    external[2].venue.missing_fills = True
    for _ in range(3):
        tick(external, cid)
    assert len(external[2].venue.posts) == 1
    with external[3]() as s:
        assert s.get(NewsCycle, cid).payload["proceeds_cents"] == 0
    external[2].venue.missing_fills = False
    finish(external, cid)


def test_terminal_order_is_refreshed_before_a_residual_sale_is_sent(external, session):
    cid = begin(external, session)
    tick(external, cid)
    with external[3]() as s:
        oid = s.scalar(select(ExchangeOrder.id))
    execution.reconcile(external[3], external[2], oid)
    external[2].venue.finish_resting(oid)  # late matching-engine update after an earlier terminal REST snapshot
    status, data = tick(external, cid)
    assert status == "BUYING" and data["sells"][0]["filled"] == 100
    assert len(external[2].venue.posts) == 1


def test_crash_after_send_claim_without_a_visible_order_never_resubmits(external, session, monkeypatch):
    cid = begin(external, session)
    original = execution.dispatch
    monkeypatch.setattr(execution, "dispatch", lambda *args: None)
    tick(external, cid)
    with external[3]() as s:
        order = s.scalar(select(ExchangeOrder))
        order.status = "SUBMITTING"
        s.commit()
    monkeypatch.setattr(execution, "dispatch", original)
    for _ in range(3):
        tick(external, cid)
    assert external[2].venue.posts == []


def test_disabling_automation_requests_cancellation_of_inflight_cycle(client, external, session):
    external[2].venue.rest_first = True
    cid = begin(external, session)
    tick(external, cid)
    prefix = f"/exchange/baskets/{external[1].id}"
    response = client.put(prefix + "/automation", json={"enabled": False})
    assert response.status_code == 200
    tick(external, cid)
    status, data = tick(external, cid)
    assert status == "CANCELLED" and data["spent_cents"] == 0
    assert len(external[2].venue.cancels) == 1


def test_connection_imports_only_existing_etf_positions_without_trading(client, portfolio, app):
    account, basket, quotes = portfolio
    venue = SimulatedVenue(account)
    app.state.exchange_gateway = execution.Gateway(venue, quotes, account.user_id, "connection-test", 100000, 1000000, 0, 1000000)
    result = client.post("/exchange/connect", json={"basket_id": basket.id})
    assert result.status_code == 200, result.text
    assert result.json()["armed"] is False and venue.posts == []
    assert client.post("/exchange/connect", json={"basket_id": basket.id}).status_code == 409


def test_connection_rejects_outside_contract_and_wrong_side(client, portfolio, app):
    account, basket, quotes = portfolio
    venue = SimulatedVenue(account)
    venue.positions["OUTSIDE-TICKER"] = 10
    app.state.exchange_gateway = execution.Gateway(venue, quotes, account.user_id, "connection-test", 100000, 1000000, 0, 1000000)
    result = client.post("/exchange/connect", json={"basket_id": basket.id})
    assert result.status_code == 409 and venue.posts == []


def test_connection_provider_failure_is_actionable_and_does_not_import_account(client, portfolio, app, session):
    account, basket, quotes = portfolio
    class UnavailableVenue:
        def account_snapshot(self):
            raise VenueError("synthetic private provider response")
    app.state.exchange_gateway = execution.Gateway(UnavailableVenue(), quotes, account.user_id, "connection-test", 100000, 1000000, 0, 1000000)
    result = client.post("/exchange/connect", json={"basket_id": basket.id})
    assert result.status_code == 503 and "synthetic private" not in result.text
    assert not session.scalar(select(Account.id).where(Account.user_id == account.user_id, Account.mode == AccountMode.LIVE))


def test_over_budget_fill_batch_cannot_partly_commit_accounting(external, session):
    cid = begin(external, session)
    for _ in range(4):
        tick(external, cid)
    venue = external[2].venue
    buy = next(i for i in venue.posts if i.action == Action.BUY)
    first = ExecutionFill("first-buy-fill", 25, Decimal("0.5"), 1250, 0)
    second = ExecutionFill("second-buy-fill", 25, Decimal("0.5"), 1250, 9999)
    venue.orders[buy.client_id] = (buy, Snapshot(buy.client_id, "canceled", 50, 0, (first, second)))
    with external[3]() as s:
        before_cash = s.get(Account, external[0].id).cash_cents
        before_bucket = news.basket_cash(s, external[0].id, external[1].id)
    execution.reconcile(external[3], external[2], buy.client_id)
    with external[3]() as s:
        order = s.get(ExchangeOrder, buy.client_id)
        assert order.status == "BLOCKED" and order.last_evidence["filled_count"] == 50
        assert order.booked_count == 0
        assert s.get(Account, external[0].id).cash_cents == before_cash
        assert news.basket_cash(s, external[0].id, external[1].id) == before_bucket
        assert not s.scalar(select(ExchangeFill.id).where(ExchangeFill.order_id == order.id))


def test_production_activation_is_separate_and_cannot_use_meta_credentials():
    from app.config import Settings
    assert execution.make_gateway(Settings(_env_file=None, live_enabled=False), object()) is None
    with pytest.raises(news.NewsError, match="separate explicit activation"):
        execution.make_gateway(Settings(_env_file=None, live_enabled=True, live_environment="production"), object())
    with pytest.raises(news.NewsError, match="exchange credentials"):
        execution.make_gateway(Settings(_env_file=None, live_enabled=True, meta_api_key="synthetic-meta-key"), object())


def test_credential_rotation_cannot_reuse_saved_exchange_scope(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from app.config import Settings
    key = ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "synthetic-key.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    config = dict(_env_file=None, live_enabled=True, live_owner_id=1, live_private_key_path=str(path),
                  live_max_order_cents=10000, live_max_cycle_cents=100000,
                  live_max_daily_turnover_cents=100000, live_max_fee_per_contract_cents=1,
                  live_experimental_model_accepted=True)
    first = execution.make_gateway(Settings(**config, live_key_id="synthetic-key-one"), object())
    second = execution.make_gateway(Settings(**config, live_key_id="synthetic-key-two"), object())
    try:
        assert first.scope != second.scope
        assert "synthetic-key" not in first.scope
    finally:
        first.venue.http.close()
        second.venue.http.close()
