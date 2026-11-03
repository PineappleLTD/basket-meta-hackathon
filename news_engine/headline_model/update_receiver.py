"""Receive Muse-agent updates. No news provider, training or model calls."""
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse


def validate_update(article, now=None):
    """A source URL is provenance supplied by the authorized agent, not proof of truth."""
    if not isinstance(article, dict):
        raise ValueError('Supply an article object.')
    headline, summary = article.get('headline'), article.get('summary', '')
    if not isinstance(headline, str) or not headline.strip() or len(headline) > 2000:
        raise ValueError('Supply an actual headline up to 2,000 characters.')
    if not isinstance(summary, str) or len(summary) > 12000:
        raise ValueError('Summary must be text up to 12,000 characters.')
    source_url = article.get('url')
    if not isinstance(source_url, str):
        raise ValueError('Supply the source article URL.')
    url = urlparse(source_url)
    if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password:
        raise ValueError('Supply a source URL without embedded credentials.')
    try:
        published = datetime.fromisoformat(article['published_at'].replace('Z', '+00:00'))
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ValueError('Supply the source publication timestamp in ISO 8601 format.') from None
    now = now or datetime.now(timezone.utc)
    if published.tzinfo is None or not 0 <= (now-published).total_seconds() <= 3600:
        raise ValueError('Publication time must have a timezone and fall within the last hour. Do not substitute the time Muse found the story.')
    published = published.astimezone(timezone.utc).isoformat()
    identity = hashlib.sha256((source_url+'|'+headline.strip()+'|'+published).encode()).hexdigest()
    return {'id': identity, 'headline': headline.strip(), 'summary': summary, 'url': source_url, 'published_at': published, 'received_at': now.isoformat(), 'provider': 'Muse app/agent'}


class UpdateQueue:
    """Append-only, at-least-once delivery with persisted duplicate suppression."""
    def __init__(self, state_path):
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(state_path))
        self.db.execute('CREATE TABLE IF NOT EXISTS seen_updates (stream TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(stream,id))')
        self.db.commit()

    def append(self, articles, output):
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            with output.open('a') as queue:
                fcntl.flock(queue.fileno(), fcntl.LOCK_EX)
                try:
                    for article in articles:
                        if self.db.execute('SELECT 1 FROM seen_updates WHERE stream=? AND id=?', (str(output), article['id'])).fetchone():
                            continue
                        queue.write(json.dumps(article)+'\n')
                        self.db.execute('INSERT INTO seen_updates VALUES (?,?)', (str(output), article['id']))
                        count += 1
                    queue.flush()
                    os.fsync(queue.fileno())
                finally:
                    fcntl.flock(queue.fileno(), fcntl.LOCK_UN)
        return count

    def close(self):
        self.db.close()
