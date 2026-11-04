"""Configured lookback windows reach discovery, staging, and saved-model scoring."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from headline_model.muse_matcher import validate_assessment
from headline_model.predictor import Predictor
from headline_model.records import Article as ModelArticle
from news_agent.agent import run_agent
from news_agent.core import AgentError, Article, Store
from news_agent.providers import HTTP, collect
from news_agent.scoring import score_staged
from test_news_agent import ToolFake


NOW = datetime(2026, 11, 4, 21, tzinfo=timezone.utc)
TOPIC = {'id': 'republicans', 'category': 'Politics', 'query': 'Republicans election',
         'alias_groups': [['Republicans']], 'contract': {
             'platform': 'kalshi', 'market_id': 'PRES-2028', 'outcome': 'YES',
             'question': 'Will Republicans win the 2028 presidential election?',
             'resolution_rules': 'YES if the Republican nominee wins the 2028 presidential election.',
             'deadline': '2028-11-08T00:00:00+00:00', 'thesis': 'Republicans win in 2028.'}}


def source(age=timedelta(hours=3), **changes):
    return {'headline': 'Republicans face election setback',
            'summary': 'Official election results show a Republican defeat.',
            'published_at': (NOW - age).isoformat(), 'url': 'https://example.org/election',
            'source': 'Fixture source', **changes}


class EvidenceClient:
    model = 'muse-spark-1.3'

    def __init__(self):
        self.calls = 0

    def assess(self, article, position):
        self.calls += 1
        return validate_assessment({
            'related': True, 'confidence': .95, 'reason': 'Relevant election update.',
            'evidence': [article['headline']], 'adverse_evidence': .9,
            'favorable_evidence': 0, 'uncertainty': .1,
            'event_specificity': .95, 'time_alignment': .9,
        }, article, self.model)


def test_predictor_explicit_day_window_accepts_earlier_news_without_changing_artifact(tmp_path):
    client = EvidenceClient()
    default = Predictor(cache_path=tmp_path/'default.sqlite3', client=client)
    daily = Predictor(cache_path=tmp_path/'daily.sqlite3', client=client, max_age_seconds=86400)
    try:
        excluded = default.predict(source(), TOPIC['contract'], now=NOW)
        assert excluded['reason'] == 'stale_article_or_expired_contract'
        assert excluded['max_age_seconds'] == 3600
        assert client.calls == 0
        accepted = daily.predict(source(), TOPIC['contract'], now=NOW)
        assert accepted['classification'] != 'ABSTAIN'
        assert accepted['max_age_seconds'] == 86400
        assert accepted['artifact_sha256'] == default.artifact_hash
        assert client.calls == 1
    finally:
        default.close()
        daily.close()


@pytest.mark.parametrize('article,contract', [
    (source(age=timedelta(days=1, seconds=1)), TOPIC['contract']),
    (source(age=timedelta(seconds=-1)), TOPIC['contract']),
    (source(), {**TOPIC['contract'], 'deadline': NOW.isoformat()}),
])
def test_day_window_still_rejects_old_future_and_expired_contracts(tmp_path, article, contract):
    client = EvidenceClient()
    predictor = Predictor(cache_path=tmp_path/'features.sqlite3', client=client, max_age_seconds=86400)
    try:
        result = predictor.predict(article, contract, now=NOW)
        assert result['classification'] == 'ABSTAIN'
        assert result['reason'] == 'stale_article_or_expired_contract'
        assert client.calls == 0
    finally:
        predictor.close()


@pytest.mark.parametrize('limit', [0, -1, 86401, True, float('nan')])
def test_invalid_lookback_is_rejected_before_model_or_source_access(tmp_path, limit):
    with pytest.raises(ValueError):
        Predictor(cache_path=tmp_path/'unused.sqlite3', client=EvidenceClient(), max_age_seconds=limit)
    for article_type in (Article, ModelArticle):
        with pytest.raises(ValueError):
            article_type.parse(source()).is_fresh(NOW, limit)


def test_collect_day_window_counts_scanned_fresh_and_unmatched_records(tmp_path):
    body = f'''<rss><channel>
        <item><title>Republicans face election setback</title><link>https://example.org/election</link>
        <pubDate>{source()['published_at']}</pubDate></item>
        <item><title>Local garden opens</title><link>https://example.org/garden</link>
        <pubDate>{source()['published_at']}</pubDate></item></channel></rss>'''.encode()
    class Response(io.BytesIO):
        status = 200
    store = Store(tmp_path/'agent.sqlite3')
    http = HTTP(store, 2, opener=lambda *_a, **_k: Response(body), clock=lambda: NOW)
    feeds = [{'name': 'Fixture', 'url': 'https://example.org/rss'}]
    try:
        default = collect(store, http, [TOPIC], 'rss', feeds=feeds, now=NOW)
        assert default['candidates'] == []
        assert default['records_seen'] == 2
        assert default['fresh_records'] == 0
        assert default['stale_or_future'] == 2
        daily = collect(store, http, [TOPIC], 'rss', feeds=feeds, now=NOW, max_age=86400)
        assert len(daily['candidates']) == 1
        assert daily['records_seen'] == 2
        assert daily['fresh_records'] == 2
        assert daily['unmatched_fresh'] == 1
        assert daily['cache_hits'] == 1
    finally:
        store.close()


def test_agent_stage_tool_uses_the_explicit_window(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    article = Article.parse(source())
    ident = store.record_article(article)
    candidates = [{'article_id': ident, 'topic_ids': [TOPIC['id']], **article.__dict__}]
    def model():
        return ToolFake(responses=[AIMessage(content='', tool_calls=[{
            'name': 'stage_update', 'id': 'stage', 'args': {
                'article_id': ident, 'topic_id': TOPIC['id'], 'reason': 'Relevant election update.'}}]),
            AIMessage(content='Finished.')])
    try:
        default = run_agent(store, [TOPIC], candidates, model_budget=2, tool_budget=2,
                            model=model(), clock=lambda: NOW)
        assert default['staged'] == []
        daily = run_agent(store, [TOPIC], candidates, model_budget=2, tool_budget=2,
                          model=model(), clock=lambda: NOW, max_age_seconds=86400)
        assert daily['staged'] == [ident]
    finally:
        store.close()


def test_scoring_uses_predictor_window_and_invalidates_score_cache_on_window_change(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    ident = store.record_article(Article.parse(source(age=timedelta(minutes=30))))
    store.stage(ident, TOPIC['id'], 'Relevant election update.')
    class FakePredictor:
        artifact_hash = 'unchanged-artifact'
        max_age_seconds = 3600
        calls = 0
        def predict(self, *_args, **_kwargs):
            self.calls += 1
            return {'classification': 'NEUTRAL', 'max_age_seconds': self.max_age_seconds}
    predictor = FakePredictor()
    try:
        hourly = score_staged(store, [TOPIC], model_calls=3, predictor=predictor, clock=lambda: NOW)
        assert len(hourly['scored']) == 1
        predictor.max_age_seconds = 86400
        daily = score_staged(store, [TOPIC], model_calls=3, predictor=predictor, clock=lambda: NOW)
        assert len(daily['scored']) == 1
        later = score_staged(store, [TOPIC], model_calls=3, predictor=predictor,
                             clock=lambda: NOW+timedelta(hours=3))
        assert later['reused'] == 1
        assert predictor.calls == 2
    finally:
        store.close()
