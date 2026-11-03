"""RSS/Atom and optional NewsAPI discovery; no publisher paywall access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from defusedxml import ElementTree as XML

from .core import AgentError, Article, Store, canonical_url, matches


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authenticated requests must never forward credentials through redirects.
        return None


class HTTP:
    def __init__(self, store: Store, request_budget: int, daily_budget: int | None = None,
                 *, opener=None, sleeper=time.sleep, clock=None):
        if request_budget < 1 or (daily_budget is not None and daily_budget < 1):
            raise AgentError('Request budgets must be positive.')
        self.store, self.limit, self.daily = store, request_budget, daily_budget
        self.used = 0
        self.open = opener or build_opener(NoRedirect()).open
        self.sleep = sleeper
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def request(self, url: str, provider: str, *, headers=None, data: bytes | None = None):
        for attempt in range(3):
            if self.used >= self.limit:
                raise AgentError('Configured per-run HTTP request budget reached.')
            now = self.clock()
            self.store.reserve_request(provider, now.date().isoformat(), self.daily)
            self.used += 1
            request = Request(url, headers={'User-Agent': 'MuseHeadlineAgent/1.0', **(headers or {})},
                              data=data, method='POST' if data is not None else 'GET')
            try:
                with self.open(request, timeout=30) as response:
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        raise AgentError('Source response exceeds the 2 MB limit.')
                    return response.status, body
            except HTTPError as error:
                code = error.code
                error.close()
                if code in {429, 500, 502, 503, 504} and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise AgentError(f'{provider} returned HTTP {code}.') from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise AgentError(f'{provider} is unavailable.') from None
        raise AgentError('HTTP request failed.')


def plain(value: str) -> str:
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', ' ', value or '')).split())


def parse_feed(body: bytes, source: str) -> tuple[list[Article], int]:
    try:
        root = XML.fromstring(body)
    except Exception:
        raise AgentError('Feed is not valid safe XML.') from None
    items = [item for item in root.iter() if item.tag.rsplit('}', 1)[-1] in {'item', 'entry'}]
    articles, rejected = [], 0
    for item in items:
        def value(*names):
            for child in item:
                if child.tag.rsplit('}', 1)[-1] in names:
                    return ''.join(child.itertext()).strip()
            return ''
        link = value('link')
        for child in item:
            if child.tag.rsplit('}', 1)[-1] == 'link' and child.get('href') and child.get('rel', 'alternate') == 'alternate':
                link = child.get('href')
                break
        try:
            articles.append(Article.parse({
                'headline': plain(value('title')), 'summary': plain(value('description', 'summary', 'content'))[:12000],
                # Updated time alone is not proof of original publication time.
                'published_at': value('pubDate', 'published', 'date'), 'url': link, 'source': source,
            }))
        except (AgentError, TypeError, ValueError):
            rejected += 1
    return articles, rejected


def collect(store: Store, http: HTTP, topics: list[dict], provider: str, *, feeds=None,
            news_key: str = '', ttl: int = 120, now: datetime | None = None,
            max_age: int = 3600) -> dict:
    if ttl < 0 or max_age <= 0 or max_age > 3600:
        raise AgentError('Cache TTL must be nonnegative and headline age at most one hour.')
    now = now or datetime.now(timezone.utc)
    errors, found = [], {}
    counters = {'cache_hits': 0, 'invalid_records': 0, 'stale_or_future': 0}
    if provider == 'newsapi' and not news_key.strip():
        raise AgentError('NEWS_API_KEY is required for NewsAPI; a Meta key is not interchangeable.')
    if provider not in {'rss', 'newsapi'}:
        raise AgentError('Choose rss or newsapi.')
    if provider == 'rss' and not feeds:
        raise AgentError('RSS mode needs a configured feed list.')

    queries = [(feed['name'], canonical_url(feed['url']), None) for feed in (feeds or [])] if provider == 'rss' else [
        (topic['id'], topic['query'], topic) for topic in topics]
    for source, query, target_topic in queries:
        key = provider + ':' + hashlib.sha256(query.encode()).hexdigest()
        try:
            cached = store.cached(key, now, ttl)
            if cached is not None:
                counters['cache_hits'] += 1
                parsed = [Article.parse(item) for item in cached]
            elif provider == 'rss':
                _, body = http.request(query, 'rss')
                parsed, rejected = parse_feed(body, source)
                counters['invalid_records'] += rejected
                store.cache(key, [a.__dict__ for a in parsed], now)
            else:
                # The cache key is the query, not the changing request timestamp.
                params = {'q': query, 'from': (now - timedelta(seconds=max_age)).isoformat(),
                          'sortBy': 'publishedAt', 'pageSize': 100, 'language': 'en'}
                _, body = http.request('https://newsapi.org/v2/everything?' + urlencode(params),
                                       'newsapi', headers={'X-Api-Key': news_key})
                try:
                    data = json.loads(body)
                except (ValueError, TypeError):
                    raise AgentError('NewsAPI returned invalid JSON.') from None
                if not isinstance(data, dict) or data.get('status') != 'ok' or not isinstance(data.get('articles'), list):
                    raise AgentError('NewsAPI did not return a successful article list.')
                parsed = []
                for item in data['articles']:
                    try:
                        if not isinstance(item, dict) or not isinstance(item.get('source') or {}, dict):
                            raise AgentError('Malformed article record.')
                        parsed.append(Article.parse({
                            'headline': item.get('title'), 'summary': item.get('description') or '',
                            'published_at': item.get('publishedAt'), 'url': item.get('url'),
                            'source': (item.get('source') or {}).get('name', 'NewsAPI'),
                        }))
                    except (AgentError, TypeError, ValueError):
                        counters['invalid_records'] += 1
                store.cache(key, [a.__dict__ for a in parsed], now)
            for article in parsed:
                if not article.is_fresh(now, max_age):
                    counters['stale_or_future'] += 1
                    continue
                eligible = [target_topic] if target_topic else [topic for topic in topics if matches(article, topic)]
                if not eligible:
                    continue
                article_id = store.record_article(article)
                entry = found.setdefault(article_id, {'article_id': article_id, 'topic_ids': []})
                for topic in eligible:
                    if topic['id'] not in entry['topic_ids']:
                        entry['topic_ids'].append(topic['id'])
        except AgentError as error:
            errors.append({'source': source, 'error': str(error)})
    candidates = []
    for entry in found.values():
        article = store.article(entry['article_id'])
        # A syndicated copy may map to an earlier saved record; recheck its age.
        if article.is_fresh(now, max_age):
            candidates.append({**entry, **article.__dict__})
    candidates.sort(key=lambda item: item['published_at'], reverse=True)
    return {'candidates': candidates, 'requests_used': http.used, **counters, 'errors': errors}


def deliver(store: Store, http: HTTP, intake_url: str, token: str,
            *, now: datetime | None = None) -> dict:
    if not token.strip():
        raise AgentError('MUSE_UPDATES_TOKEN is required to deliver to the classifier.')
    canonical_url(intake_url)
    endpoint = urlsplit(intake_url)
    if endpoint.scheme != 'https' and endpoint.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise AgentError('Use HTTPS for a remote authenticated classifier intake.')
    now = now or datetime.now(timezone.utc)
    delivered, expired, errors = [], [], []
    for article_id in store.pending():
        article = store.article(article_id)
        if not article.is_fresh(now):
            store.mark(article_id, 'expired')
            expired.append(article_id)
            continue
        try:
            code, body = http.request(intake_url, 'classifier_intake',
                                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                      data=json.dumps(article.intake_payload()).encode())
            try:
                reply = json.loads(body)
            except (ValueError, TypeError):
                raise AgentError('Classifier intake returned invalid JSON.') from None
            if code != 202 or not isinstance(reply, dict) or reply.get('accepted') is not True:
                raise AgentError('Classifier intake did not confirm acceptance.')
            # A crash before this commit is safe to retry: receiver deduplicates.
            store.mark(article_id, 'delivered')
            delivered.append(article_id)
        except AgentError as error:
            errors.append({'article_id': article_id, 'error': str(error)})
            break  # Leave undelivered records pending for a later run.
    return {'delivered': delivered, 'expired': expired, 'errors': errors,
            'meaning': 'Accepted into classifier queue; not proof of scoring or execution.'}
