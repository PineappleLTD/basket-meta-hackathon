"""A news article can require independent discovery for multiple contracts."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from news_agent import core
from news_agent.core import Article, Store


def article():
    return Article.parse({"headline": "An update affects two watched contracts", "summary": "Verified source excerpt.",
                          "published_at": datetime.now(timezone.utc).isoformat(),
                          "url": "https://example.org/news", "source": "Test source"})


def test_staging_is_idempotent_per_article_topic_pair(tmp_path):
    store = Store(tmp_path / "agent.sqlite3")
    try:
        ident = store.record_article(article())
        assert store.stage(ident, "contract-a", "Relevant to A.")
        assert store.stage(ident, "contract-b", "Independently relevant to B.")
        assert not store.stage(ident, "contract-a", "Repeated A.")
        assert store.staged_pairs() == {(ident, "contract-a"), (ident, "contract-b")}
        assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
        # Public article delivery still sends one record, not one copy per topic.
        assert store.pending() == [ident]
        assert store.status()["outbox"] == {"pending": 1}
        store.mark(ident, "delivered")
        assert store.pending() == []
        assert store.outbox_status(ident) == "delivered"
        assert store.status()["outbox"] == {"delivered": 1}
    finally:
        store.close()


def test_new_topic_after_delivery_is_pending_without_losing_old_acknowledgement(tmp_path):
    store = Store(tmp_path / "agent.sqlite3")
    try:
        ident = store.record_article(article())
        store.stage(ident, "contract-a", "Relevant to A.")
        store.mark(ident, "delivered")
        assert store.stage(ident, "contract-b", "New watched contract.")
        assert store.pending() == [ident]
        assert store.outbox_status(ident) == "pending"
        assert store.status()["outbox"] == {"pending": 1}
        assert dict(store.db.execute("SELECT topic_id,status FROM outbox")) == {
            "contract-a": "delivered", "contract-b": "pending"}
    finally:
        store.close()


def test_legacy_outbox_rows_survive_pair_key_migration_and_reopen(tmp_path):
    path = tmp_path / "agent.sqlite3"
    item = article()
    with sqlite3.connect(path) as legacy:
        legacy.executescript("""
            CREATE TABLE articles (id TEXT PRIMARY KEY, story_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE outbox (article_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
                                 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
        """)
        legacy.execute("INSERT INTO articles VALUES (?,?,?)", (item.id, item.story_key, json.dumps(asdict(item))))
        legacy.execute("INSERT INTO outbox VALUES (?,?,?,?)", (item.id, "contract-a", "Existing reason.", "delivered"))
    store = Store(path)
    try:
        assert store.article(item.id) == item
        assert list(store.db.execute("SELECT article_id,topic_id,reason,status FROM outbox")) == [
            (item.id, "contract-a", "Existing reason.", "delivered")]
        assert store.stage(item.id, "contract-b", "New topic after upgrade.")
        assert not store.stage(item.id, "contract-a", "Repeated topic after upgrade.")
    finally:
        store.close()
    restored = Store(path)
    try:
        assert restored.staged_pairs() == {(item.id, "contract-a"), (item.id, "contract-b")}
        assert restored.pending() == [item.id]
        assert restored.article(item.id) == item
    finally:
        restored.close()


def test_failed_schema_swap_rolls_back_legacy_outbox(tmp_path, monkeypatch):
    path = tmp_path / "agent.sqlite3"
    original_connect = sqlite3.connect
    with original_connect(path) as legacy:
        legacy.execute("""CREATE TABLE outbox (article_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
                           reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')""")
        legacy.execute("INSERT INTO outbox VALUES ('article-a','contract-a','Preserve this.','delivered')")

    class InterruptedUpgrade(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("ALTER TABLE outbox_topic_upgrade"):
                raise sqlite3.OperationalError("Synthetic interrupted migration")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        return original_connect(*args, factory=InterruptedUpgrade, **kwargs)

    monkeypatch.setattr(core.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="Synthetic interrupted migration"):
        Store(path)
    with original_connect(path) as restored:
        assert list(restored.execute("SELECT * FROM outbox")) == [
            ("article-a", "contract-a", "Preserve this.", "delivered")]
        assert not restored.execute("SELECT name FROM sqlite_master WHERE name='outbox_topic_upgrade'").fetchone()
