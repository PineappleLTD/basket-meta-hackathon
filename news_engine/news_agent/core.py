"""Validated source records, persistent budgets, cache, and idempotent outbox."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class AgentError(ValueError):
    """Safe, user-facing failure. Never include credentials/provider bodies."""


def utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, IndexError):
            raise AgentError('Source publication timestamp is missing or invalid.') from None
    if result.tzinfo is None:
        raise AgentError('Source publication timestamp must include a timezone.')
    return result.astimezone(timezone.utc)


def normalize(value: str) -> str:
    return ' '.join(re.findall(r'\w+', value.casefold()))


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise AgentError('Source URL must be an HTTP(S) URL without credentials.')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/',
                       urlencode(sorted(query)), ''))


@dataclass(frozen=True)
class Article:
    headline: str
    summary: str
    published_at: str
    url: str
    source: str

    @classmethod
    def parse(cls, value: dict) -> 'Article':
        headline = value.get('headline', '')
        summary = value.get('summary', '')
        if not isinstance(headline, str) or not normalize(headline) or len(headline) > 2000:
            raise AgentError('Source headline must contain text and be at most 2,000 characters.')
        if not isinstance(summary, str) or len(summary) > 12000:
            raise AgentError('Source summary must be at most 12,000 characters.')
        published = utc(value.get('published_at', '')).isoformat()
        return cls(headline.strip(), summary, published,
                   canonical_url(value.get('url', '')), str(value.get('source', '')))

    @property
    def id(self) -> str:
        return hashlib.sha256((self.source_key + '|' + self.headline + '|' + self.summary).encode()).hexdigest()

    @property
    def source_key(self) -> str:
        return hashlib.sha256((self.url + '|' + self.published_at).encode()).hexdigest()

    @property
    def story_key(self) -> str:
        # Collapse exact syndicated records, without suppressing changed facts.
        return hashlib.sha256((normalize(self.headline) + '|' + normalize(self.summary)
                               + '|' + self.published_at).encode()).hexdigest()

    def is_fresh(self, now: datetime, max_age: int = 3600) -> bool:
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 86400:
            raise AgentError('Headline age must be between one second and 24 hours.')
        return 0 <= (now - utc(self.published_at)).total_seconds() <= max_age

    def intake_payload(self) -> dict:
        return {'article': {key: value for key, value in asdict(self).items() if key != 'source'}}


def load_topics(path: Path) -> list[dict]:
    """Accept either a topic watchlist or the original classifier portfolio schema."""
    data = json.loads(path.read_text())
    topics = data.get('topics')
    if topics is None and isinstance(data.get('positions'), list):
        topics = []
        for position in data['positions']:
            topics.append({
                'id': position['id'], 'category': position.get('category', 'Uncategorized'),
                'alias_groups': position['relevance_groups'],
                'query': ' AND '.join('(' + ' OR '.join('"' + a.replace('"', '') + '"'
                                                      for a in group) + ')'
                                      for group in position['relevance_groups']),
                'contract': {key: position[key] for key in
                             ['platform', 'market_id', 'outcome', 'question', 'thesis',
                              'resolution_rules', 'deadline']},
            })
    if not isinstance(topics, list) or not topics:
        raise AgentError('Provide a nonempty topics or positions list.')
    seen = set()
    for topic in topics:
        tid = topic.get('id')
        groups = topic.get('alias_groups')
        if not isinstance(tid, str) or not tid.strip() or tid in seen:
            raise AgentError('Topic IDs must be unique, nonempty strings.')
        seen.add(tid)
        if not isinstance(groups, list) or not groups or any(
            not isinstance(group, list) or not group or any(
                not isinstance(alias, str) or not normalize(alias) for alias in group)
            for group in groups
        ):
            raise AgentError('Each topic needs nonempty alias groups.')
        query = topic.get('query', '')
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise AgentError('Each topic needs a search query of 1–500 characters.')
        if 'contract' in topic:
            contract = topic['contract']
            if contract.get('platform') not in {'kalshi', 'polymarket'} or contract.get('outcome') not in {'YES', 'NO'}:
                raise AgentError('Contract platform/held side is invalid.')
            if any(not isinstance(contract.get(key), str) or not contract[key].strip()
                   for key in ['market_id', 'question', 'resolution_rules', 'deadline']):
                raise AgentError('Contract metadata cannot be blank.')
            utc(contract['deadline'])
    return topics


def matches(article: Article, topic: dict) -> bool:
    text = ' ' + normalize(article.headline + ' ' + article.summary) + ' '
    return all(any(' ' + normalize(alias) + ' ' in text for alias in group)
               for group in topic['alias_groups'])


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=20)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY, story_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_identity (
                key TEXT PRIMARY KEY, article_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, provider TEXT NOT NULL, day TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                article_id TEXT NOT NULL, topic_id TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY (article_id, topic_id));
        ''')
        self.db.commit()
        self._migrate_outbox_pairs()

    def _migrate_outbox_pairs(self):
        """Upgrade the original article-only key without discarding staged work."""
        try:
            with self.db:
                # Recheck the schema after obtaining the write lock: another
                # process may have upgraded this shared store in the meantime.
                self.db.execute('BEGIN IMMEDIATE')
                columns = self.db.execute('PRAGMA table_info(outbox)').fetchall()
                primary = [column[1] for column in sorted(columns, key=lambda column: column[5]) if column[5]]
                if primary == ['article_id', 'topic_id']:
                    return
                if primary != ['article_id']:
                    raise AgentError('Unsupported news outbox schema; existing records were left unchanged.')
                self.db.execute('''CREATE TABLE outbox_topic_upgrade (
                    article_id TEXT NOT NULL, topic_id TEXT NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY (article_id, topic_id))''')
                self.db.execute('''INSERT INTO outbox_topic_upgrade (article_id,topic_id,reason,status)
                                   SELECT article_id,topic_id,reason,status FROM outbox ORDER BY rowid''')
                self.db.execute('DROP TABLE outbox')
                self.db.execute('ALTER TABLE outbox_topic_upgrade RENAME TO outbox')
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def record_article(self, article: Article) -> str:
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO articles VALUES (?,?,?)',
                            (article.id, article.story_key, json.dumps(asdict(article))))
            saved = self.db.execute('SELECT id FROM articles WHERE id=? OR story_key=?',
                                    (article.id, article.story_key)).fetchone()[0]
            self.db.execute('INSERT OR IGNORE INTO source_identity VALUES (?,?)', (article.source_key, saved))
            return saved

    def article(self, article_id: str) -> Article:
        with self.lock:
            row = self.db.execute('SELECT payload FROM articles WHERE id=?', (article_id,)).fetchone()
        if not row:
            raise AgentError('Unknown source article ID.')
        return Article.parse(json.loads(row[0]))

    def cached(self, key: str, now: datetime, ttl: int):
        with self.lock:
            row = self.db.execute('SELECT created,payload FROM cache WHERE key=?', (key,)).fetchone()
        if row and 0 <= now.timestamp() - row[0] < ttl:
            return json.loads(row[1])
        return None

    def cache(self, key: str, payload, now: datetime):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                            (key, now.timestamp(), json.dumps(payload)))

    def reserve_request(self, provider: str, day: str, daily_limit: int | None):
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            count = self.db.execute('SELECT COUNT(*) FROM requests WHERE provider=? AND day=?',
                                    (provider, day)).fetchone()[0]
            if daily_limit is not None and count >= daily_limit:
                raise AgentError('Configured daily provider request budget reached.')
            self.db.execute('INSERT INTO requests(provider,day) VALUES (?,?)', (provider, day))

    def stage(self, article_id: str, topic_id: str, reason: str) -> bool:
        with self.lock, self.db:
            article = self.article(article_id)
            prior = self.db.execute('SELECT article_id FROM source_identity WHERE key=?',
                                    (article.source_key,)).fetchone()
            status = 'pending'
            if prior and prior[0] != article_id and self.article(prior[0]).headline == article.headline:
                # The existing receiver ignores summary-only revisions in its ID.
                # Preserve the new evidence for review instead of pretending it ran.
                status = 'needs_revision_review'
            cur = self.db.execute('INSERT OR IGNORE INTO outbox(article_id,topic_id,reason,status) VALUES (?,?,?,?)',
                                  (article_id, topic_id, reason, status))
            return bool(cur.rowcount)

    def outbox_status(self, article_id: str) -> str | None:
        with self.lock:
            states = {r[0] for r in self.db.execute('SELECT status FROM outbox WHERE article_id=?', (article_id,))}
        return self._article_status(states)

    @staticmethod
    def _article_status(states) -> str | None:
        # Keep the article-based delivery API: one new topic makes the article
        # pending again, while a revision requiring review must never be sent.
        return next((status for status in ('needs_revision_review', 'pending', 'delivered', 'expired')
                     if status in states), min(states) if states else None)

    def _article_statuses(self):
        grouped = {}
        for article_id, status in self.db.execute('SELECT article_id,status FROM outbox ORDER BY rowid'):
            grouped.setdefault(article_id, set()).add(status)
        return {article_id: self._article_status(states) for article_id, states in grouped.items()}

    def staged_pairs(self) -> set[tuple[str, str]]:
        with self.lock:
            return set(self.db.execute('SELECT article_id,topic_id FROM outbox'))

    def pending(self) -> list[str]:
        with self.lock:
            return [article_id for article_id, status in self._article_statuses().items() if status == 'pending']

    def mark(self, article_id: str, status: str):
        with self.lock, self.db:
            self.db.execute('UPDATE outbox SET status=? WHERE article_id=?', (status, article_id))

    def status(self) -> dict:
        with self.lock:
            statuses = {}
            for status in self._article_statuses().values():
                statuses[status] = statuses.get(status, 0) + 1
            return {'articles': self.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],
                    'outbox': statuses}
