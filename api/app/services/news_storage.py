"""Durable news evidence and collector checkpoints for hosts with ephemeral disks."""

from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import zlib

from sqlalchemy import delete

from app.models import utcnow
from app.news_models import NewsAgentCheckpoint, NewsEvidenceCache, NewsEvidenceFailure


class DatabaseFeatureCache:
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, row, model):
        from headline_model.features import cached_assessment, key_for

        with self.sessions() as session:
            item = session.get(NewsEvidenceCache, key_for(row, model))
            return cached_assessment(item.payload, row, model) if item else None

    def put(self, row, assessment):
        from headline_model.features import key_for

        key = key_for(row, assessment.model)
        with self.sessions() as session:
            session.merge(NewsEvidenceCache(key=key, payload=asdict(assessment), updated_at=utcnow()))
            session.execute(delete(NewsEvidenceFailure).where(NewsEvidenceFailure.key == key))
            session.commit()

    def failure(self, row, model):
        from headline_model.features import key_for

        with self.sessions() as session:
            item = session.get(NewsEvidenceFailure, key_for(row, model))
            return item.error if item else None

    def quarantine(self, row, model, error):
        from headline_model.features import key_for

        with self.sessions() as session:
            session.merge(NewsEvidenceFailure(key=key_for(row, model), error=error, updated_at=utcnow()))
            session.commit()

    def close(self):
        # Each operation owns its session; the application owns the connection pool.
        pass


def evidence_cache(settings, sessions):
    if not settings.news_database_state:
        return None
    if sessions is None:
        raise ValueError("Database-backed news storage requires the app session factory.")
    return DatabaseFeatureCache(sessions)


def save_agent(sessions, path):
    """Capture committed SQLite work, including WAL pages, as one database row."""
    path = Path(path)
    if not path.is_file():
        return
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        payload = snapshot.serialize()
    finally:
        snapshot.close()
        source.close()
    digest = hashlib.sha256(payload).hexdigest()
    with sessions() as session:
        saved = session.get(NewsAgentCheckpoint, 1)
        if saved is not None and saved.sha256 == digest:
            return
        session.merge(NewsAgentCheckpoint(id=1, payload=zlib.compress(payload),
                                         sha256=digest, updated_at=utcnow()))
        session.commit()


def restore_agent(sessions, path):
    """Called only by the collector holding the instance lock, before opening its store."""
    with sessions() as session:
        saved = session.get(NewsAgentCheckpoint, 1)
        if saved is None:
            return
        payload = zlib.decompress(saved.payload)
        if hashlib.sha256(payload).hexdigest() != saved.sha256:
            raise ValueError("The saved collector checkpoint failed its integrity check.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".restore", delete=False) as file:
            temporary = Path(file.name)
            file.write(payload)
        connection = sqlite3.connect(temporary)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The saved collector checkpoint is not a valid database.")
        finally:
            connection.close()
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
