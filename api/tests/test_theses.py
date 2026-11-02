import json

import httpx
import pytest

from app.llm import LLMError, MetaLlama, parse_json_object
from app.models import Market, MarketPrice


class FakeLLM:
    """Replies with `expand`, then with `select` built from the candidate ids it was shown."""

    def __init__(self, keywords, choose):
        self.keywords = keywords
        self.choose = choose
        self.calls = []

    def complete_json(self, system, user, schema):
        self.calls.append(user)
        if len(self.calls) == 1:
            return {"summary": "Rates fall.", "sub_themes": ["Fed", "Inflation"], "keywords": self.keywords}
        lines = user.split("Candidates:\n", 1)[1].splitlines()
        candidates = {int(line.split(" | ")[0]): line for line in lines}
        return {"picks": self.choose(candidates)}


@pytest.fixture
def use_llm(app, client):
    def install(llm):
        app.state.llm = llm
        return llm
    return install


def _ids_by_ticker(session):
    return {m.external_id: m.id for m in session.query(Market)}


def test_select_returns_weighted_positions_that_build_a_basket(client, session, use_llm):
    ids = _ids_by_ticker(session)
    fed, cpi = ids["MOCK-FEDCUT-26DEC"], ids["MOCK-CPI-26NOV-B3"]
    llm = use_llm(FakeLLM(
        ["fed", "rates", "cpi", "Fed "],
        lambda c: [
            {"market_id": fed, "side": "yes", "relevance": 0.9, "rationale": "Direct."},
            {"market_id": cpi, "side": "YES", "relevance": 0.6, "rationale": "Inflation down."},
            {"market_id": 99999, "side": "yes", "relevance": 1, "rationale": "Hallucinated."},
            {"market_id": fed, "side": "no", "relevance": 0.5, "rationale": "Duplicate."},
        ],
    ))

    resp = client.post("/theses/select", json={"thesis": "The Fed cuts rates as inflation cools"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Only markets mentioning the keywords were offered to the model.
    offered = llm.calls[1]
    assert "Fed cut rates" in offered and "Bitcoin" not in offered
    assert body["keywords"] == ["fed", "rates", "cpi"]
    assert body["candidates_considered"] == 2

    picks = body["picks"]
    assert [p["market"]["id"] for p in picks] == [fed, cpi]
    assert sum(p["weight"] for p in picks) == pytest.approx(1)
    assert sum(p["risk_contribution"] for p in picks) == pytest.approx(1)
    assert all(p["volatility"] > 0 and 0 < p["liquidity"] <= 1 for p in picks)
    assert body["volatility"] > 0 and body["diversification_ratio"] >= 1 - 1e-9
    assert body["positions"] == [
        {"market_id": p["market"]["id"], "side": "yes", "weight": pytest.approx(p["weight"])} for p in picks
    ]

    basket = client.post("/baskets", json={"name": "Rate cuts", "positions": body["positions"]})
    assert basket.status_code == 201
    assert basket.json()["nav"] == pytest.approx(100)


def test_select_caps_positions_and_drops_weak_picks(client, session, use_llm):
    use_llm(FakeLLM(
        ["will"],
        lambda c: [
            {"market_id": i, "side": "no", "relevance": r, "rationale": ""}
            for i, r in zip(c, [0.9, 0.8, 0.7, 0.2])
        ],
    ))
    body = client.post("/theses/select", json={"thesis": "Nothing happens", "max_positions": 2}).json()
    assert sorted(p["relevance"] for p in body["picks"]) == [0.8, 0.9]
    assert {p["side"] for p in body["picks"]} == {"no"}


def test_select_respects_max_weight(client, use_llm):
    use_llm(FakeLLM(["will"], lambda c: [{"market_id": i, "side": "yes", "relevance": 0.9} for i in c]))
    body = client.post("/theses/select", json={"thesis": "Everything", "max_weight": 0.15}).json()
    assert len(body["picks"]) >= 5
    assert max(p["weight"] for p in body["picks"]) <= 0.15 + 1e-9


def test_select_errors(client, use_llm):
    assert client.post("/theses/select", json={"thesis": "x"}).status_code == 422

    use_llm(None)
    assert client.post("/theses/select", json={"thesis": "Rates fall"}).status_code == 503

    use_llm(FakeLLM(["zebra migration"], lambda c: []))
    resp = client.post("/theses/select", json={"thesis": "Zebras"})
    assert resp.status_code == 422
    assert "No open markets" in resp.json()["detail"]

    use_llm(FakeLLM(["fed"], lambda c: [{"market_id": i, "side": "yes", "relevance": 0.1} for i in c]))
    assert client.post("/theses/select", json={"thesis": "Rates fall"}).status_code == 422

    class Down:
        def complete_json(self, *a):
            raise LLMError("boom")

    use_llm(Down())
    assert client.post("/theses/select", json={"thesis": "Rates fall"}).status_code == 502


def test_select_skips_near_certain_markets(client, session, use_llm):
    market = session.query(Market).filter_by(external_id="MOCK-FEDCUT-26DEC").one()
    market.yes_price = 0.995
    session.commit()
    llm = use_llm(FakeLLM(["fed"], lambda c: [{"market_id": i, "side": "yes", "relevance": 1} for i in c]))
    assert client.post("/theses/select", json={"thesis": "Rates fall"}).status_code == 422
    assert len(llm.calls) == 1


def _price_points(session):
    return session.query(MarketPrice).count()


def _select(client, use_llm):
    """A thesis request with a fresh FakeLLM, which answers one request."""
    use_llm(FakeLLM(["fed"], lambda c: [{"market_id": i, "side": "yes", "relevance": 0.9} for i in c]))
    return client.post("/theses/select", json={"thesis": "Rates fall"})


def test_select_syncs_first_when_markets_are_stale(app, client, session, use_llm):
    before = _price_points(session)
    _select(client, use_llm)
    assert _price_points(session) == before  # the startup sync is fresh

    app.state.synced_at -= 16 * 60
    _select(client, use_llm)
    after = _price_points(session)
    assert after > before
    _select(client, use_llm)
    assert _price_points(session) == after


def test_select_sync_can_be_turned_off(app, client, session, use_llm):
    app.state.settings.sync_max_age_minutes = 0
    app.state.synced_at = None
    before = _price_points(session)
    _select(client, use_llm)
    assert _price_points(session) == before


def test_select_carries_on_when_the_sync_fails(app, client, use_llm, monkeypatch):
    def down():
        raise RuntimeError("Kalshi down")

    monkeypatch.setattr(app.state.market_source, "fetch_markets", down)
    app.state.synced_at = None
    assert _select(client, use_llm).status_code == 200
    assert app.state.synced_at is None


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! {"a": 1} Hope that helps.'])
def test_parse_json_object(text):
    assert parse_json_object(text) == {"a": 1}


def test_meta_llama_client_sends_schema_and_parses_reply():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    http = httpx.Client(
        base_url="https://llama.test", transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer k"}
    )
    llm = MetaLlama("k", "some-model", http=http)
    assert llm.complete_json("sys", "user", {"type": "object"}) == {"ok": True}
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "some-model"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == {"type": "object"}

    failing = MetaLlama("k", "m", http=httpx.Client(
        base_url="https://llama.test", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key"))
    ))
    with pytest.raises(LLMError, match="401"):
        failing.complete_json("s", "u", {})


def test_illiquid_sides_are_not_offered_or_kept(client, session, use_llm):
    markets = {m.external_id: m for m in session.query(Market)}
    agi, oai, fed = markets["MOCK-AGI-30"], markets["MOCK-OAIFRONTIER-27"], markets["MOCK-FEDCUT-26DEC"]
    # A 20c spread on a 14c YES is unusable, but the same spread on the 86c NO is tolerable.
    agi.yes_bid, agi.yes_ask = 0.05, 0.25
    # No quotes at all: neither side is tradeable.
    oai.yes_bid = oai.yes_ask = None
    session.commit()

    llm = use_llm(FakeLLM(
        ["agi", "openai", "fed"],
        lambda c: [
            {"market_id": agi.id, "side": "yes", "relevance": 0.9, "rationale": ""},
            {"market_id": fed.id, "side": "yes", "relevance": 0.8, "rationale": ""},
        ],
    ))
    body = client.post("/theses/select", json={"thesis": "AI and rate cuts"}).json()

    lines = {int(line.split(" | ")[0]): line for line in llm.calls[1].split("Candidates:\n", 1)[1].splitlines()}
    assert oai.id not in lines
    assert lines[agi.id].endswith("| no")
    assert lines[fed.id].endswith("| yes,no")
    assert [p["market"]["id"] for p in body["picks"]] == [fed.id]
