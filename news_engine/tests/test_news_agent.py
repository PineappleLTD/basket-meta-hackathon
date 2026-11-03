import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import httpx

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from news_agent.agent import make_model, run_agent
from news_agent.core import AgentError, Article, Store, canonical_url, load_topics, matches, utc
from news_agent.providers import HTTP, collect, deliver, parse_feed

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
TOPIC = {'id': 'fed', 'category': 'Economics', 'query': 'Federal Reserve AND rates',
         'alias_groups': [['Federal Reserve', 'FOMC'], ['rates', 'inflation']]}


def article(**changes):
    values = {'headline': 'Federal Reserve discusses interest rates', 'summary': 'Source feed excerpt.',
              'published_at': (NOW - timedelta(minutes=5)).isoformat(),
              'url': 'https://example.org/news?utm_source=test', 'source': 'Source'}
    return Article.parse({**values, **changes})


def rss(timestamp=None):
    date = timestamp or (NOW - timedelta(minutes=5)).isoformat()
    return f'''<rss><channel><item><title>Federal Reserve discusses interest rates</title>
    <link>https://example.org/news</link><pubDate>{date}</pubDate>
    <description>&lt;p&gt;An actual feed excerpt.&lt;/p&gt;</description></item></channel></rss>'''.encode()


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status = status


class ToolFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def http(self, opener, requests=10, daily=None):
        return HTTP(self.store, requests, daily, opener=opener,
                    sleeper=lambda _: None, clock=lambda: NOW)

    def test_original_timestamps_required(self):
        for date in ['', '2026-10-26T03:00:00', 'not-a-date']:
            with self.assertRaises(AgentError):
                article(published_at=date)
        self.assertEqual(utc('Sun, 25 Oct 2026 23:00:00 -0400'), NOW)

    def test_url_tracking_dedup_keeps_meaningful_parameters(self):
        self.assertEqual(canonical_url('https://EXAMPLE.org/a?b=2&utm_source=x&a=1#top'),
                         'https://example.org/a?a=1&b=2')
        with self.assertRaises(AgentError):
            article(url='https://secret:password@example.org')

    def test_both_alias_groups_must_match(self):
        self.assertTrue(matches(article(), TOPIC))
        self.assertFalse(matches(article(headline='Federal Reserve releases meeting calendar'), TOPIC))

    def test_feed_has_original_date_and_plain_excerpt(self):
        parsed, bad = parse_feed(rss(), 'Source')
        self.assertEqual(bad, 0)
        self.assertEqual(parsed[0].summary, 'An actual feed excerpt.')
        self.assertEqual(utc(parsed[0].published_at), NOW - timedelta(minutes=5))

    def test_atom_updated_is_not_publication_date(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>News</title><link href="https://example.org/a"/><updated>2026-10-26T03:00:00Z</updated></entry></feed>'
        self.assertEqual(parse_feed(body, 'Source'), ([], 1))

    def test_entity_expansion_is_rejected(self):
        body = b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><item><title>&x;</title></item></rss>'
        with self.assertRaises(AgentError):
            parse_feed(body, 'Source')

    def test_cache_and_stale_recheck(self):
        calls = []
        def opener(request, **kwargs):
            calls.append(request.full_url)
            return Response(rss())
        http = self.http(opener)
        feeds = [{'name': 'Test', 'url': 'https://example.org/rss'}]
        one = collect(self.store, http, [TOPIC], 'rss', feeds=feeds, now=NOW, ttl=7200)
        two = collect(self.store, http, [TOPIC], 'rss', feeds=feeds, now=NOW, ttl=7200)
        later = collect(self.store, http, [TOPIC], 'rss', feeds=feeds,
                        now=NOW + timedelta(hours=1), ttl=7200)
        self.assertEqual(len(one['candidates']), 1)
        self.assertEqual(two['cache_hits'], 1)
        self.assertEqual(later['candidates'], [])
        self.assertEqual(len(calls), 1)

    def test_syndicated_title_and_outbox_are_idempotent(self):
        first = self.store.record_article(article())
        second = self.store.record_article(article(url='https://other.example/news'))
        self.assertEqual(first, second)
        self.assertTrue(self.store.stage(first, 'fed', 'Relevant.'))
        self.assertFalse(self.store.stage(second, 'fed', 'Same story.'))
        self.assertEqual(self.store.status()['outbox'], {'pending': 1})

    def test_material_revisions_are_preserved_instead_of_silently_deduplicated(self):
        first = self.store.record_article(article())
        changed = self.store.record_article(article(summary='New facts in a revised source excerpt.'))
        self.assertNotEqual(first, changed)
        self.store.stage(first, 'fed', 'Original.')
        self.store.stage(changed, 'fed', 'Changed evidence.')
        self.assertEqual(self.store.outbox_status(changed), 'needs_revision_review')
        self.assertEqual(self.store.article(changed).summary, 'New facts in a revised source excerpt.')

    def test_new_publication_time_is_a_new_source_record(self):
        first = self.store.record_article(article())
        later = self.store.record_article(article(published_at=(NOW-timedelta(minutes=1)).isoformat()))
        self.assertNotEqual(first, later)
        self.store.stage(later, 'fed', 'New publication.')
        self.assertEqual(self.store.outbox_status(later), 'pending')

    def test_daily_budget_survives_new_client(self):
        opener = lambda *a, **k: Response(b'ok')
        self.http(opener, daily=1).request('https://example.org', 'rss')
        with self.assertRaises(AgentError):
            self.http(opener, daily=1).request('https://example.org', 'rss')

    def test_retries_count_against_request_budget(self):
        calls = []
        def failed(request, **kwargs):
            calls.append(1)
            raise HTTPError(request.full_url, 503, 'unavailable', {}, io.BytesIO(b'provider secret'))
        http = self.http(failed, requests=2)
        with self.assertRaisesRegex(AgentError, 'budget reached'):
            http.request('https://example.org', 'rss')
        self.assertEqual(len(calls), 2)

    def test_auth_errors_do_not_echo_key_or_body(self):
        def failed(request, **kwargs):
            raise HTTPError(request.full_url, 401, 'credential-is-secret', {}, io.BytesIO(b'credential-is-secret'))
        with self.assertRaises(AgentError) as caught:
            self.http(failed).request('https://example.org', 'newsapi', headers={'X-Api-Key': 'credential-is-secret'})
        self.assertEqual(str(caught.exception), 'newsapi returned HTTP 401.')

    def test_newsapi_key_is_separate_and_request_uses_header(self):
        with self.assertRaises(AgentError):
            collect(self.store, self.http(None), [TOPIC], 'newsapi', now=NOW)
        calls = []
        def opener(request, **kwargs):
            calls.append(request)
            a = article()
            payload = {'status': 'ok', 'articles': [{'title': a.headline, 'description': a.summary,
                        'publishedAt': a.published_at, 'url': a.url, 'source': {'name': 'Source'}}]}
            return Response(json.dumps(payload).encode())
        result = collect(self.store, self.http(opener), [TOPIC], 'newsapi',
                          news_key='different-provider-key', now=NOW)
        self.assertEqual(len(result['candidates']), 1)
        self.assertNotIn('different-provider-key', calls[0].full_url)
        self.assertEqual(calls[0].get_header('X-api-key'), 'different-provider-key')

    def test_delivery_requires_confirmed_202_and_is_idempotent(self):
        a = article()
        ident = self.store.record_article(a)
        self.store.stage(ident, 'fed', 'Relevant.')
        requests = []
        def opener(request, **kwargs):
            requests.append(request)
            self.assertEqual(json.loads(request.data), a.intake_payload())
            self.assertEqual(request.get_header('Authorization'), 'Bearer receiving-token')
            return Response(b'{"accepted":true,"queued":true}', 202)
        http = self.http(opener)
        one = deliver(self.store, http, 'http://127.0.0.1:8765/v1/muse/updates', 'receiving-token', now=NOW)
        two = deliver(self.store, http, 'http://127.0.0.1:8765/v1/muse/updates', 'receiving-token', now=NOW)
        self.assertEqual(one['delivered'], [ident])
        self.assertEqual(two['delivered'], [])
        self.assertEqual(len(requests), 1)

    def test_failed_delivery_stays_pending_after_restart(self):
        ident = self.store.record_article(article())
        self.store.stage(ident, 'fed', 'Relevant.')
        result = deliver(self.store, self.http(lambda *a, **k: Response(b'{"accepted":true}', 200)),
                         'http://127.0.0.1:8765/v1/muse/updates', 'token', now=NOW)
        self.assertTrue(result['errors'])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.pending(), [ident])

    def test_stale_outbox_does_not_rewrite_time_or_send(self):
        ident = self.store.record_article(article(published_at=(NOW - timedelta(hours=2)).isoformat()))
        self.store.stage(ident, 'fed', 'Old.')
        def never(*a, **k):
            self.fail('Stale article must not make a network call.')
        result = deliver(self.store, self.http(never), 'http://127.0.0.1:8765/v1/muse/updates', 'token', now=NOW)
        self.assertEqual(result['expired'], [ident])
        self.assertEqual(self.store.pending(), [])

    def test_remote_intake_requires_encryption(self):
        with self.assertRaises(AgentError):
            deliver(self.store, self.http(None), 'http://remote.example/updates', 'token', now=NOW)

    def test_real_langchain_graph_stages_only_known_ids(self):
        a = article()
        ident = self.store.record_article(a)
        fake = ToolFake(responses=[AIMessage(content='', tool_calls=[
            {'name': 'stage_update', 'args': {'article_id': 'invented', 'topic_id': 'fed', 'reason': 'Fake'}, 'id': 'bad'},
            {'name': 'stage_update', 'args': {'article_id': ident, 'topic_id': 'fed', 'reason': 'Relevant rate discussion'}, 'id': 'good'},
        ]), AIMessage(content='Finished.')])
        result = run_agent(self.store, [TOPIC], [{'article_id': ident, 'topic_ids': ['fed'], **a.__dict__}],
                           model_budget=3, tool_budget=4, model=fake, clock=lambda: NOW)
        self.assertEqual(result['staged'], [ident])
        self.assertEqual(self.store.pending(), [ident])
        again = run_agent(self.store, [TOPIC], [{'article_id': ident, 'topic_ids': ['fed'], **a.__dict__}],
                          model_budget=3, tool_budget=4, model=fake, clock=lambda: NOW)
        self.assertEqual(again['considered'], 0)

    def test_meta_responses_request_serializes_without_live_provider_access(self):
        captured = []
        def respond(request):
            captured.append({'host': request.url.host, 'path': request.url.path,
                             'payload': json.loads(request.content)})
            return httpx.Response(200, json={
                'id': 'resp_local', 'object': 'response', 'created_at': 1,
                'model': 'muse-spark-1.3', 'status': 'completed',
                'output': [{'id': 'msg_local', 'type': 'message', 'status': 'completed',
                            'role': 'assistant', 'content': [{'type': 'output_text',
                            'text': 'Ready.', 'annotations': []}]}],
                'parallel_tool_calls': True, 'tool_choice': 'auto', 'tools': [],
            })
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with patch.dict('os.environ', {'MODEL_API_KEY': 'local-test-placeholder', 'MUSE_MODEL': 'muse-spark-1.3'}):
                result = make_model(http_client=client).invoke('Local serialization check.')
        self.assertTrue(result.content)
        self.assertEqual(captured[0]['host'], 'api.meta.ai')
        self.assertEqual(captured[0]['path'], '/v1/responses')
        self.assertEqual(captured[0]['payload']['max_output_tokens'], 4096)
        self.assertFalse(captured[0]['payload']['store'])

    def test_model_budget_stops_a_tool_loop(self):
        ident = self.store.record_article(article())
        call = AIMessage(content='', tool_calls=[{'name': 'stage_update', 'args': {
            'article_id': 'invented', 'topic_id': 'fed', 'reason': 'No'}, 'id': 'bad'}])
        fake = ToolFake(responses=[call, call, call])
        result = run_agent(self.store, [TOPIC], [{'article_id': ident, 'topic_ids': ['fed']}],
                           model_budget=2, tool_budget=10, model=fake, clock=lambda: NOW)
        self.assertEqual(fake.i, 2)
        self.assertEqual(result['staged'], [])

    def test_portfolio_schema_loads_both_platforms_without_trading_fields(self):
        path = Path(self.temp.name) / 'portfolio.json'
        positions = [{'id': platform, 'platform': platform, 'market_id': 'exact-market',
                      'outcome': 'NO', 'question': 'Exact question?', 'thesis': 'Held NO thesis',
                      'resolution_rules': 'Exact rules.', 'deadline': '2026-12-31T23:59:00Z',
                      'relevance_groups': [['Entity'], ['Event']]} for platform in ['kalshi', 'polymarket']]
        path.write_text(json.dumps({'positions': positions}))
        topics = load_topics(path)
        self.assertEqual(len(topics), 2)
        self.assertEqual(topics[1]['contract']['outcome'], 'NO')


if __name__ == '__main__':
    unittest.main()
