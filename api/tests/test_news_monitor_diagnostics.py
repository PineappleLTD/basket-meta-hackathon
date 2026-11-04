"""A healthy poller must not imply that evidence was assessed or trades occurred."""
from pathlib import Path
from datetime import timedelta

from app.models import utcnow
from app.news_models import NewsMonitorStatus, NewsSignal
from app.services import news, news_execution
from news_agent.core import Store
from sqlalchemy import delete, select

from tests.test_news_monitor import Response, install_sources, monitor, portfolio


def test_successful_cycle_reports_actual_pipeline_counts(monitor, portfolio, session):
    counts, _, _ = install_sources(monitor, portfolio, session)
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        note = monitor.status(session)["note"]
        assert "candidate" in note
        assert "assessment" in note
        assert str(counts["evidence"]) in note
        assert "24 hours" in note
    finally:
        store.close()


def test_empty_feeds_report_zero_assessments_instead_of_implying_analysis(monitor, portfolio, session):
    monitor.source_opener = lambda *_a, **_k: Response(b"<rss><channel/></rss>")
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        note = monitor.status(session)["note"]
        assert "0 matched candidate" in note
        assert "0 new assessment" in note
        assert monitor._remaining("discovery") == 10
        assert monitor._remaining("evidence") == 10
    finally:
        store.close()


def test_provider_failure_reports_safe_reason_without_secret(monitor, portfolio, session):
    install_sources(monitor, portfolio, session, discovery_error=True)
    monitor._run()
    session.expire_all()
    note = session.get(NewsMonitorStatus, 1).note
    assert "authentication" in note
    assert "401" in note
    assert "provider-secret" not in note


def test_incompatible_optional_engine_does_not_break_error_reporting(monitor, portfolio, session, monkeypatch):
    import news_agent.agent as agent
    install_sources(monitor, portfolio, session)
    monkeypatch.delattr(agent, "DiscoveryError")
    def old_factory(**_kwargs):
        raise TypeError("provider-secret incompatible argument")
    monitor.model_factory = old_factory
    monitor._run()
    session.expire_all()
    saved = session.get(NewsMonitorStatus, 1)
    assert saved.status == "PAUSED_ERROR"
    assert "incompatible" in saved.note
    assert "provider-secret" not in saved.note


def test_same_day_news_is_scored_delivered_and_can_propose_with_its_saved_expiry(monitor, portfolio, session):
    session.execute(delete(NewsSignal))
    session.commit()
    counts, _, _ = install_sources(monitor, portfolio, session, age_seconds=6 * 3600)
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        session.expire_all()
        signals = session.scalars(select(NewsSignal)).all()
        assert counts["evidence"] > 0
        assert signals
        assert all(s.payload["result"]["max_age_seconds"] == 86400 for s in signals)
        assert news.available_signals(session, portfolio[1])
        assert news.proposal(session, portfolio[0], portfolio[1])["signal_ids"]
        cycle = news_execution.start(session, *portfolio)
        assert news.utc(cycle.expires_at) > utcnow() + timedelta(hours=17)
        assert news.utc(cycle.expires_at) <= min(news.signal_expires_at(s) for s in signals)
        assert not news.available_signals(session, portfolio[1], now=utcnow() + timedelta(hours=19))
    finally:
        store.close()


def test_legacy_signals_keep_one_hour_expiry(portfolio, session):
    signal = session.get(NewsSignal, "a" * 64)
    signal.published_at = utcnow() - timedelta(hours=2)
    session.commit()
    assert news.available_signals(session, portfolio[1]) == []


def test_same_day_window_never_admits_future_news(monitor, portfolio, session):
    session.execute(delete(NewsSignal))
    session.commit()
    counts, _, _ = install_sources(monitor, portfolio, session, age_seconds=-60)
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        assert counts["discovery"] == counts["evidence"] == 0
        assert session.scalars(select(NewsSignal)).all() == []
    finally:
        store.close()
