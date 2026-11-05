"""Free-host restarts must retain evidence, collector work, and spent call budgets."""
from dataclasses import asdict
from datetime import timedelta
import importlib.util
from pathlib import Path
import zlib

import pytest

from app.models import utcnow
from app.news_models import NewsAgentCheckpoint, NewsEvidenceCache
from app.services.news_monitor import NewsMonitor
from app.services.news_storage import DatabaseFeatureCache, restore_agent, save_agent
from headline_model.features import key_for
from headline_model.muse_matcher import validate_assessment
from news_agent.core import Article, Store
from tests.test_news_monitor import install_sources, monitor, portfolio


def example():
    row = {
        "headline": "Officials ruled out the condition.", "summary": "An official statement.",
        "published_at": utcnow().isoformat(), "market_question": "Will the condition occur?",
        "resolution_rules": "YES if the condition occurs before the deadline.",
        "deadline": (utcnow() + timedelta(days=30)).isoformat(),
        "outcome": "YES", "position_thesis": "The condition occurs.",
    }
    assessment = validate_assessment({
        "related": True, "confidence": 0.95, "reason": "Direct official statement.",
        "evidence": [row["headline"]], "adverse_evidence": 1.0,
        "favorable_evidence": 0.0, "uncertainty": 0.1,
        "event_specificity": 0.95, "time_alignment": 0.95,
    }, row, "muse-spark-1.3")
    return row, assessment


def test_evidence_and_quarantine_survive_new_cache_instances(app, client):
    row, assessment = example()
    cache = DatabaseFeatureCache(app.state.sessionmaker)
    assert cache.get(row, assessment.model) is None
    cache.quarantine(row, assessment.model, "Synthetic invalid evidence")
    assert cache.failure(row, assessment.model) == "Synthetic invalid evidence"
    cache.put(row, assessment)
    cache.put(row, assessment)
    cache.close()
    resumed = DatabaseFeatureCache(app.state.sessionmaker)
    assert asdict(resumed.get(row, assessment.model)) == asdict(assessment)
    assert resumed.failure(row, assessment.model) is None


def test_database_evidence_retains_provenance_checks(app, client):
    row, assessment = example()
    cache = DatabaseFeatureCache(app.state.sessionmaker)
    cache.put(row, assessment)
    with app.state.sessionmaker() as session:
        item = session.get(NewsEvidenceCache, key_for(row, assessment.model))
        item.payload = {**item.payload, "model": "wrong-model"}
        session.commit()
    with pytest.raises(ValueError, match="provenance"):
        cache.get(row, assessment.model)


def test_collector_checkpoint_restores_wal_and_staged_work(app, client, tmp_path):
    path = tmp_path / "collector.sqlite3"
    store = Store(path)
    store.db.execute("PRAGMA journal_mode=WAL")
    article = Article.parse({"headline": "Officials confirmed the condition.", "summary": "",
                             "published_at": utcnow().isoformat(),
                             "url": "https://example.org/news", "source": "Synthetic"})
    article_id = store.record_article(article)
    store.stage(article_id, "topic-one", "Direct evidence")
    save_agent(app.state.sessionmaker, path)
    store.close()
    path.unlink()
    restore_agent(app.state.sessionmaker, path)
    restored = Store(path)
    try:
        assert restored.article(article_id) == article
        assert restored.staged_pairs() == {(article_id, "topic-one")}
        assert restored.pending() == [article_id]
    finally:
        restored.close()


def test_bad_checkpoint_preserves_existing_local_file(app, client, tmp_path):
    path = tmp_path / "collector.sqlite3"
    path.write_bytes(b"existing-local-file")
    with app.state.sessionmaker() as session:
        session.add(NewsAgentCheckpoint(id=1, payload=zlib.compress(b"invalid"), sha256="0" * 64))
        session.commit()
    with pytest.raises(ValueError, match="integrity"):
        restore_agent(app.state.sessionmaker, path)
    assert path.read_bytes() == b"existing-local-file"


def test_restart_after_ephemeral_file_loss_reuses_completed_paid_work(monitor, portfolio, session):
    monitor.settings.news_database_state = True
    counts, _, _ = install_sources(monitor, portfolio, session)

    def run_once(item):
        cycle = item.cycle

        def once(store):
            cycle(store)
            item.stop.set()

        item.cycle = once
        item._run()

    run_once(monitor)
    assert counts["discovery"] == 1
    assert counts["evidence"] > 0
    spent = counts.copy()
    remaining = {kind: monitor._remaining(kind) for kind in ("discovery", "evidence")}
    Path(monitor.settings.news_monitor_state).unlink()
    assert not Path(monitor.settings.news_feature_cache).exists()
    resumed = NewsMonitor(monitor.settings, monitor.sessions, monitor.engine,
                          source_opener=monitor.source_opener,
                          evidence_opener=monitor.evidence_opener,
                          discovery_transport=monitor.discovery_transport)
    run_once(resumed)
    assert counts == spent
    assert {kind: resumed._remaining(kind) for kind in remaining} == remaining
    assert resumed.local_status == "STOPPED"
    assert resumed.checked_at is not None


def test_free_host_cannot_silently_fall_back_to_ephemeral_sqlite(monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/run_production.py"
    spec = importlib.util.spec_from_file_location("basket_production_guard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {"BASKET_REQUIRE_POSTGRES": "true", "PMETF_DATABASE_URL": "sqlite://"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        module.prepare_database(env)


def test_collector_requires_direct_neon_connection(monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/run_production.py"
    spec = importlib.util.spec_from_file_location("basket_pooling_guard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {"PMETF_DATABASE_URL": "postgresql://synthetic:synthetic@ep-test-pooler.us-east-2.aws.neon.tech/basket",
           "PMETF_NEWS_DATABASE_STATE": "true"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="direct connection"):
        module.prepare_database(env)
