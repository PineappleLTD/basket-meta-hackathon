"""Discovery selections keep their contract identity and budget priority."""
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage

from news_agent.agent import run_agent
from news_agent.core import Article, Store
from news_agent.scoring import score_staged
from test_news_agent import ToolFake


NOW = datetime(2026, 11, 4, 21, tzinfo=timezone.utc)


def article(suffix='first'):
    return Article.parse({'headline': 'Republicans face election setback',
                          'summary': f'Verified election update {suffix}.',
                          'published_at': (NOW-timedelta(minutes=10)).isoformat(),
                          'url': 'https://example.org/'+suffix, 'source': 'Fixture'})


def topic(ident):
    return {'id': ident, 'alias_groups': [['Republicans']], 'query': 'Republicans election',
            'contract': {'platform': 'kalshi', 'market_id': ident, 'outcome': 'YES',
                         'question': 'Will Republicans win?', 'resolution_rules': 'Exact election rules.',
                         'deadline': '2028-11-08T00:00:00+00:00'}}


def test_previously_staged_article_can_be_selected_for_another_basket(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    item = article()
    ident = store.record_article(item)
    store.stage(ident, 'basket-one', 'Already assessed for the first basket.')
    model = ToolFake(responses=[AIMessage(content='', tool_calls=[{
        'name': 'stage_update', 'id': 'new-basket', 'args': {
            'article_id': ident, 'topic_id': 'basket-two', 'reason': 'Independent contract relevance.'}}]),
        AIMessage(content='Finished.')])
    try:
        result = run_agent(store, [topic('basket-one'), topic('basket-two')],
                           [{'article_id': ident, 'topic_ids': ['basket-one', 'basket-two']}],
                           model_budget=2, tool_budget=2, model=model, clock=lambda: NOW)
        assert result['staged'] == [ident]
        assert store.staged_pairs() == {(ident, 'basket-one'), (ident, 'basket-two')}
        again = run_agent(store, [topic('basket-one'), topic('basket-two')],
                          [{'article_id': ident, 'topic_ids': ['basket-one', 'basket-two']}],
                          model_budget=2, tool_budget=2, model=model, clock=lambda: NOW)
        assert again['considered'] == 0
    finally:
        store.close()


def test_explicit_fourth_topic_gets_first_assessment_with_one_call_budget(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    ident = store.record_article(article())
    store.stage(ident, 'desired-fourth', 'Model selected this exact contract.')
    topics = [topic('broad-first'), topic('broad-second'), topic('broad-third'), topic('desired-fourth')]
    class Predictor:
        artifact_hash = 'unchanged-artifact'
        calls = 0
        seen = []
        def predict(self, item, contract, **kwargs):
            if self.calls >= 1:
                return {'classification': 'ABSTAIN', 'reason': 'model_call_budget_reached'}
            self.calls += 1
            self.seen.append(contract['market_id'])
            return {'classification': 'NEUTRAL'}
    predictor = Predictor()
    try:
        result = score_staged(store, topics, model_calls=1, predictor=predictor, clock=lambda: NOW)
        assert predictor.seen == ['desired-fourth']
        assert result['scored'][0]['topic_id'] == 'desired-fourth'
    finally:
        store.close()


def test_unselected_lexical_matches_do_not_spend_any_evidence_calls(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    old = store.record_article(article('older-record'))
    new = store.record_article(article('newer-record'))
    store.stage(old, 'explicit-old', 'Older explicit selection.')
    store.stage(new, 'explicit-new', 'Newer explicit selection.')
    topics = [topic('fallback'), topic('explicit-old'), topic('explicit-new')]
    class Predictor:
        artifact_hash = 'unchanged-artifact'
        calls = 0
        def __init__(self):
            self.seen = []
        def predict(self, item, contract, **kwargs):
            self.calls += 1
            self.seen.append((item['url'], contract['market_id']))
            return {'classification': 'NEUTRAL'}
    predictor = Predictor()
    try:
        score_staged(store, topics, model_calls=20, predictor=predictor, clock=lambda: NOW)
        assert predictor.seen == [('https://example.org/newer-record', 'explicit-new'),
                                  ('https://example.org/older-record', 'explicit-old')]
        assert predictor.calls == 2
    finally:
        store.close()
