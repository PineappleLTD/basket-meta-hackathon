"""Overlapping political baskets are screened independently without starvation."""
import json
from pathlib import Path

import httpx
import pytest

from app.models import utcnow
from news_agent.core import Store
from tests.test_news_monitor import Response, monitor, portfolio


def screening_prompt(value):
    """Read the supplied screening data from the actual model HTTP request."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        if "watchlist" in value and "source_records" in value:
            return value
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for item in values:
        found = screening_prompt(item)
        if found is not None:
            return found
    return None


@pytest.fixture
def overlapping_baskets(monitor, portfolio, session, client):
    older = portfolio[1]
    older.description = "Democrats win the 2028 election."
    session.commit()
    response = client.post("/baskets", json={
        "name": "Republican election basket",
        "description": "Republicans win the 2028 election.",
        "positions": [{"market_id": p.market_id, "side": p.side.value}
                      for p in older.positions],
    })
    assert response.status_code == 201, response.text
    newer_id = response.json()["id"]
    published = utcnow().isoformat()
    body = ("<rss><channel><item><title>Republicans and Democrats review election results</title>"
            "<description>Both parties assess the latest official election results.</description>"
            "<link>https://example.org/shared-election-update</link>"
            f"<pubDate>{published}</pubDate></item></channel></rss>").encode()
    monitor.source_opener = lambda *_args, **_kwargs: Response(body)
    monitor.evidence_opener = lambda *_args, **_kwargs: pytest.fail("Unstaged news must not spend evidence calls")
    prompts = []

    def complete(request):
        prompt = screening_prompt(json.loads(request.content))
        assert prompt is not None
        prompts.append(prompt)
        return httpx.Response(200, json={
            "id": f"resp_batch_{len(prompts)}", "object": "response", "created_at": 1,
            "model": "muse-spark-1.3", "status": "completed",
            "output": [{"id": f"msg_batch_{len(prompts)}", "type": "message",
                        "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "Screening completed.", "annotations": []}]}],
        })

    monitor.discovery_transport = httpx.MockTransport(complete)
    return older.id, newer_id, prompts


def assert_basket_scope(prompt, basket_id):
    ids = {topic["id"] for topic in prompt["watchlist"]}
    assert ids
    assert all(topic_id.startswith(f"basket-{basket_id}-") for topic_id in ids)
    assert prompt["source_records"]
    assert all(set(record["topic_ids"]) <= ids for record in prompt["source_records"])


def test_discovery_screens_only_the_newest_basket_in_one_prompt(monitor, overlapping_baskets):
    _older_id, newer_id, prompts = overlapping_baskets
    store = Store(Path(monitor.settings.news_monitor_state))
    try:
        monitor.cycle(store)
        assert len(prompts) == 1
        assert_basket_scope(prompts[0], newer_id)
        assert monitor._remaining("discovery") == 9
        assert monitor._remaining("evidence") == 10
    finally:
        store.close()


def test_next_cycle_rotates_and_reconsiders_shared_news_for_the_other_basket(monitor, overlapping_baskets):
    older_id, newer_id, prompts = overlapping_baskets
    state_path = Path(monitor.settings.news_monitor_state)
    store = Store(state_path)
    try:
        monitor.cycle(store)
        assert_basket_scope(prompts[0], newer_id)
    finally:
        store.close()
    # A process restart must preserve rotation and per-basket considered records.
    store = Store(state_path)
    try:
        monitor.cycle(store)
        assert len(prompts) == 2
        assert_basket_scope(prompts[1], older_id)
        first_articles = {item["article_id"] for item in prompts[0]["source_records"]}
        second_articles = {item["article_id"] for item in prompts[1]["source_records"]}
        assert first_articles == second_articles
        assert len(first_articles) == 1
        monitor.cycle(store)
        assert len(prompts) == 2  # Neither basket repeatedly screens the same unchanged story.
        assert monitor._remaining("discovery") == 8
        assert monitor._remaining("evidence") == 10
    finally:
        store.close()
