"""Offline acceptance tests exercise the real LangChain -> Muse -> saved Ridge path."""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError

import httpx
import pytest
from app.config import Settings
from app.db import init_db, make_engine
from app.main import create_app
from app.models import utcnow
from app.news_models import (
    NewsCycle,
    NewsMonitorStatus,
    NewsMonitorUsage,
    NewsPolicy,
    NewsSignal,
)
from app.services import news, news_execution
from app.services.news_monitor import (
    BudgetReached,
    InstanceLock,
    MonitorStopped,
    NewsMonitor,
)
from fastapi.testclient import TestClient
from news_agent.core import Article, Store
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from tests import test_news as news_fixtures
from tests.fake_kalshi import FakeKalshi

portfolio = news_fixtures.portfolio


class Response(io.BytesIO):
    status = 200


@pytest.fixture
def monitor(app, client, tmp_path):
    settings = app.state.settings.model_copy(update={
        "news_monitor_enabled": True, "meta_api_key": "synthetic-key",
        "news_monitor_state": str(tmp_path / "agent.sqlite3"),
        "news_feature_cache": str(tmp_path / "features.sqlite3"),
    })
    item = NewsMonitor(settings, app.state.sessionmaker, app.state.sessionmaker.kw["bind"])
    assert item._initialize()
    return item


def install_sources(monitor, portfolio, session, *, evidence_error=False, discovery_error=False):
    target = news.watchlist(session, portfolio[1])["topics"][0]
    article = Article.parse({"headline": "Official setback: " + target["contract"]["question"] + " Condition ruled out.",
                             "summary": "An official statement rules out the condition.",
                             "published_at": utcnow().isoformat(), "url": "https://example.org/official",
                             "source": "Synthetic source"})
    # The real source parser constructs this same identity (source name is not part of its ID).
    body = (f"<rss><channel><item><title>{article.headline}</title>"
            f"<description>{article.summary}</description><link>{article.url}</link>"
            f"<pubDate>{article.published_at}</pubDate></item></channel></rss>").encode()
    counts = {"source": 0, "discovery": 0, "evidence": 0}

    def source(request, **kwargs):
        counts["source"] += 1
        return Response(body)

    def discovery(request):
        counts["discovery"] += 1
        if discovery_error:
            return httpx.Response(401, json={"error": {"message": "provider-secret", "type": "authentication_error"}})
        payload = json.loads(request.content)
        assert request.url.host == "api.meta.ai"
        assert payload["store"] is False
        assert {tool["name"] for tool in payload["tools"]} == {"stage_update"}
        if counts["discovery"] == 1:
            output = [{"id": "fc_stage", "type": "function_call", "call_id": "call_stage",
                       "name": "stage_update", "status": "completed", "arguments": json.dumps({
                           "article_id": article.id, "topic_id": target["id"], "reason": "Official evidence concerns this condition."})}]
        else:
            output = [{"id": "msg_done", "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": "Finished.", "annotations": []}]}]
        return httpx.Response(200, json={"id": f"resp_{counts['discovery']}", "object": "response",
                                       "created_at": 1, "model": "muse-spark-1.3", "status": "completed",
                                       "output": output, "parallel_tool_calls": True})

    def evidence(request, **kwargs):
        counts["evidence"] += 1
        if evidence_error:
            raise HTTPError(request.full_url, 503, "provider-secret", {}, io.BytesIO(b"provider-secret"))
        payload = json.loads(request.data)
        data = json.loads(payload["input"])
        related = data["market_question"] == target["contract"]["question"]
        assessment = {"related": related, "confidence": 0.95, "reason": "Synthetic official evidence",
                      "evidence": [article.headline] if related else [], "adverse_evidence": 1.0 if related else 0,
                      "favorable_evidence": 0, "uncertainty": 0.1, "event_specificity": 0.95, "time_alignment": 0.9}
        return Response(json.dumps({"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(assessment)}]}]}).encode())

    monitor.source_opener = source
    monitor.discovery_transport = httpx.MockTransport(discovery)
    monitor.evidence_opener = evidence
    monitor._sleep = lambda _: None
    return counts, article, target


def test_real_discovery_evidence_ridge_delivery_and_existing_auto_execution(monitor, portfolio, session, app, client):
    session.execute(delete(NewsSignal))  # Only evidence from this cycle may drive the test.
    session.get(NewsPolicy, portfolio[1].id).mode = "auto"
    session.commit()
    counts, _article, _target = install_sources(monitor, portfolio, session)
    with_store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(with_store)
        assert monitor.local_status == "MONITORING"
        assert counts["discovery"] == 2
        assert with_store.pending()
        assert 1 <= counts["evidence"] <= 3
        assert portfolio[2].requests == []  # News agent has no order capability.
        session.expire_all()
        saved = session.scalars(select(NewsSignal)).all()
        assert any(s.payload["result"]["classification"] == "ADVERSE" for s in saved)
        assert all(s.payload["result"]["feature_cache_hit"] for s in saved)
        assert all(s.basket_id == portfolio[1].id for s in saved)
        monitor.cycle(with_store)
        assert counts["discovery"] == 2
        assert session.scalar(select(NewsMonitorUsage.attempts).where(NewsMonitorUsage.kind == "discovery")) == 2
        assert len(session.scalars(select(NewsSignal)).all()) == len(saved)
        # The existing price-update automation is unchanged and consumes these signals.
        news_execution.run(session, app.state.brokers)
        cycle = session.scalar(select(NewsCycle))
        assert cycle.status == "SELLING"
        assert cycle.payload["sells"][0]["filled"] == 40
        assert not any(o.action.value == "buy" for o in portfolio[2].requests)
        news_execution.run(session, app.state.brokers)
        assert cycle.status == "BUYING"
        assert cycle.payload["sells"][0]["filled"] == 100
        news_execution.run(session, app.state.brokers)
        assert cycle.status == "COMPLETE"
        assert {o.market.id for o in portfolio[2].requests} <= {p.market_id for p in portfolio[1].positions}
        assert cycle.payload["spent_cents"] <= cycle.payload["proceeds_cents"]
        assert client.get("/health").status_code == 200
    finally:
        with_store.close()


def test_evidence_retry_is_counted_and_blocked_before_exceeding_cap(monitor, portfolio, session):
    monitor.settings.news_monitor_evidence_limit = 1
    counts, _, _ = install_sources(monitor, portfolio, session, evidence_error=True)
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        assert counts["evidence"] == 1
        assert monitor._remaining("evidence") == 0
        assert monitor.local_status == "BUDGET_EXHAUSTED"
        assert store.db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
        assert portfolio[2].requests == []
    finally:
        store.close()


def test_paid_provider_failure_pauses_persistently_without_breaking_app(monitor, portfolio, session, app, client, caplog):
    counts, _, _ = install_sources(monitor, portfolio, session, discovery_error=True)
    monitor._run()
    assert counts["discovery"] == 1
    assert counts["evidence"] == 0
    assert monitor.local_status == "PAUSED_ERROR"
    assert "provider-secret" not in caplog.text
    resumed = NewsMonitor(monitor.settings, monitor.sessions, monitor.engine)
    resumed._run()  # A normal restart does not repeatedly retry a failed paid provider.
    assert resumed.local_status == "PAUSED_ERROR"
    app.state.news_monitor = monitor
    response = client.get(f"/baskets/{portfolio[1].id}/climate")
    assert response.status_code == 200
    assert response.json()["monitoring"]["status"] == "PAUSED_ERROR"
    assert "provider-secret" not in response.text
    assert client.get("/health").status_code == 200
    assert client.get("/accounts/paper").status_code == 200


def test_no_baskets_or_expired_contracts_do_not_make_requests(monitor, portfolio, session):
    for p in portfolio[1].positions:
        p.market.is_open = False
    session.commit()
    monitor.source_opener = lambda *_a, **_k: pytest.fail("No source request expected")
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        assert monitor.local_status == "WAITING_FOR_BASKETS"
    finally:
        store.close()


def test_changed_constituents_are_rechecked_before_delivery(monitor, portfolio, session):
    original = monitor._watchlists()
    portfolio[1].positions.clear()
    session.commit()
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        store.db.execute("CREATE TABLE scores (key TEXT, article_id TEXT, topic_id TEXT)")
        item = Article.parse({"headline": "Synthetic setback", "summary": "", "published_at": utcnow().isoformat(),
                              "url": "https://example.org/a", "source": "Synthetic"})
        aid = store.record_article(item)
        with store.db:
            store.db.execute("INSERT INTO scores VALUES (?,?,?)", ("a", aid, original[portfolio[1].id][0]["id"]))
        before = len(session.scalars(select(NewsSignal)).all())
        monitor._deliver(store, original)
        assert len(session.scalars(select(NewsSignal)).all()) == before
    finally:
        store.close()


def test_budget_reservations_are_atomic_and_survive_restart(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'app.db'}", str(tmp_path / "shared.db"))
    init_db(engine)
    sessions = sessionmaker(engine)
    settings = Settings(_env_file=None, news_monitor_discovery_limit=3)
    monitor = NewsMonitor(settings, sessions, engine)
    monitor._initialize()
    def reserve(_):
        try:
            monitor._reserve("discovery")
            return True
        except BudgetReached:
            return False
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(reserve, range(16))) == 3
        restarted = NewsMonitor(settings, sessions, engine)
        restarted._initialize()
        assert restarted._remaining("discovery") == 0
        with pytest.raises(BudgetReached):
            restarted._reserve("discovery")
        restarted.stop.set()
        with pytest.raises(MonitorStopped):
            restarted._reserve("evidence")
    finally:
        engine.dispose()


def test_only_one_worker_can_acquire_database_lock(monitor, tmp_path):
    # Different state paths still share the same PostgreSQL advisory lock; file SQLite
    # uses the app database path (in-memory SQLite uses the supplied test state path).
    path = tmp_path / "agent.db"
    first, second = InstanceLock(monitor.engine, path), InstanceLock(monitor.engine, path)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        first.close()
        second.close()


def test_startup_and_shutdown_own_worker_without_blocking_health(tmp_path):
    settings = Settings(_env_file=None, meta_api_key="synthetic-key",
                        database_url=f"sqlite:///{tmp_path / 'app.db'}", shared_database=str(tmp_path / "shared.db"),
                        news_monitor_state=str(tmp_path / "agent.db"),
                        sync_interval_minutes=0, live_refresh_seconds=0, backfill_days=0)
    app = create_app(settings, FakeKalshi(seed=0))
    with TestClient(app) as client:
        worker = app.state.news_monitor
        deadline = time.monotonic() + 3
        while worker.local_status not in ("WAITING_FOR_BASKETS", "PAUSED_ERROR") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.local_status == "WAITING_FOR_BASKETS"
        assert worker.thread.is_alive()
        assert client.get("/health").status_code == 200
        assert client.get("/markets").status_code == 200
    assert worker.stop.is_set()
    assert not worker.thread.is_alive()


def test_status_has_no_other_owners_basket_details(monitor, portfolio, session):
    monitor._status("MONITORING", "Checking news.", checked=True)
    result = monitor.status(session)
    assert result["requests"]["discovery"] == {"used": 0, "limit": 10}
    assert "basket" not in result and "topics" not in result
    session.get(NewsMonitorStatus, 1).updated_at = utcnow() - timedelta(days=1)
    session.commit()
    assert monitor.status(session)["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("enabled,key,status", [(False, "synthetic-key", "DISABLED"), (True, None, "WAITING_FOR_KEY")])
def test_disabled_or_unconfigured_worker_starts_no_thread(monitor, session, enabled, key, status):
    monitor.settings.news_monitor_enabled = enabled
    monitor.settings.meta_api_key = key
    monitor.start()
    assert monitor.thread is None
    assert monitor.status(session)["status"] == status


def test_zero_budget_starts_no_source_or_model_requests(monitor, portfolio, session):
    monitor.settings.news_monitor_discovery_limit = 0
    monitor.source_opener = lambda *_a, **_k: pytest.fail("Budget must be checked before fetching sources")
    monitor._run()
    assert monitor.local_status == "BUDGET_EXHAUSTED"
    assert monitor._remaining("evidence") == 10


def test_file_database_lock_cannot_be_bypassed_with_another_state_path(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'app.db'}")
    first = InstanceLock(engine, tmp_path / "first.db")
    second = InstanceLock(engine, tmp_path / "second.db")
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        first.close()
        second.close()
        engine.dispose()
