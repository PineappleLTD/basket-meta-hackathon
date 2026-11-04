"""An incomplete provider reply must not look like completed discovery."""
import json

import httpx
import pytest
from langchain_core.messages import AIMessage

from news_agent.agent import DiscoveryError, make_model, run_agent
from news_agent.core import Store
from test_discovery_priority import NOW, article, topic
from test_news_agent import ToolFake


def test_truncated_response_is_diagnostic_not_successful_empty_discovery(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    ident = store.record_article(article())
    def incomplete(request):
        return httpx.Response(200, json={
            'id': 'resp_truncated', 'object': 'response', 'created_at': 1,
            'model': 'muse-spark-1.3', 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens', 'private': 'provider-secret'},
            'output': [], 'usage': {'input_tokens': 12, 'output_tokens': 4096, 'total_tokens': 4108},
        })
    try:
        with httpx.Client(transport=httpx.MockTransport(incomplete)) as client:
            model = make_model(http_client=client, api_key='secret-auth-token')
            with pytest.raises(DiscoveryError, match='incomplete_response'):
                run_agent(store, [topic('selected')], [{'article_id': ident, 'topic_ids': ['selected']}],
                          model_budget=1, tool_budget=2, model=model, clock=lambda: NOW)
        saved = store.cached('monitor:last_discovery', NOW, 120)
        assert saved['status'] == 'incomplete'
        assert saved['incomplete_reason'] == 'max_output_tokens'
        assert saved['input_tokens'] == 12
        assert saved['output_tokens'] == 4096
        assert saved['total_tokens'] == 4108
        assert 'provider-secret' not in json.dumps(saved)
        assert 'secret-auth-token' not in json.dumps(saved)
        assert store.pending() == []
    finally:
        store.close()


def test_completed_discovery_returns_only_allowlisted_completion_metadata(tmp_path):
    store = Store(tmp_path/'agent.sqlite3')
    ident = store.record_article(article())
    model = ToolFake(responses=[AIMessage(content='provider-secret', response_metadata={
        'status': 'completed', 'finish_reason': 'stop', 'provider_payload': 'provider-secret'},
        usage_metadata={'input_tokens': 8, 'output_tokens': 2, 'total_tokens': 10})])
    try:
        result = run_agent(store, [topic('selected')], [{'article_id': ident, 'topic_ids': ['selected']}],
                           model_budget=1, tool_budget=2, model=model, clock=lambda: NOW)
        assert result['completion_status'] == 'completed'
        assert result['finish_reason'] == 'stop'
        saved = store.cached('monitor:last_discovery', NOW, 120)
        assert saved['total_tokens'] == 10
        assert 'provider-secret' not in json.dumps(saved)
        assert 'provider_payload' not in saved
    finally:
        store.close()
