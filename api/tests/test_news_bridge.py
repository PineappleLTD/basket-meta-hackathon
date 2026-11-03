import pytest
from sqlalchemy.orm import object_session

from app.services import news
from news_bridge import AppBridge
from news_agent.core import Store, Article, load_topics
from tests.test_news import article, portfolio


@pytest.mark.parametrize("url", ["https://other.example", "http://other.example", "http://user:secret@localhost", "http://localhost/?token=x"])
def test_bridge_does_not_send_app_credentials_to_other_origins(url):
    with pytest.raises(ValueError, match="loopback"):
        AppBridge(url, "synthetic-token", 1)


def test_exported_etf_watchlist_loads_in_existing_news_agent(client, portfolio, tmp_path):
    bridge = AppBridge("http://127.0.0.1:8000", client.headers["Authorization"].removeprefix("Bearer "),
                       portfolio[1].id, client=client)
    result = bridge.watchlist()
    import json
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(result))
    topics = load_topics(path)
    assert len(topics) == 3
    assert {t["contract"]["market_id"] for t in topics} == {p.market.external_id for p in portfolio[1].positions}
    assert {t["contract"]["outcome"] for t in topics} == {"YES"}


def test_bridge_delivers_only_scored_articles_and_deduplicates(client, app, portfolio, tmp_path):
    class Predictor:
        artifact_hash = "synthetic-artifact"
        def predict(self, article, contract, now):
            return {"artifact_sha256": self.artifact_hash, "historical": False,
                    "classification": "ABSTAIN", "reason": "synthetic weak evidence", "confidence": 0.2}
    app.state.news_predictor = Predictor()
    bridge = AppBridge("http://127.0.0.1:8000", client.headers["Authorization"].removeprefix("Bearer "),
                       portfolio[1].id, client=client)
    topics = bridge.watchlist()["topics"]
    store = Store(tmp_path / "agent.db")
    try:
        item = Article.parse(article(headline="A second synthetic story"))
        aid = store.record_article(item)
        assert bridge.deliver(store, topics)["delivered"] == 0
        with store.db:
            store.db.execute("CREATE TABLE scores (key TEXT PRIMARY KEY, article_id TEXT, topic_id TEXT, result TEXT)")
            store.db.execute("INSERT INTO scores VALUES (?,?,?,?)", ("x", aid, topics[0]["id"], "{}"))
        assert bridge.deliver(store, topics)["delivered"] == 1
        assert bridge.deliver(store, topics)["delivered"] == 0
        climate = client.get(f"/baskets/{portfolio[1].id}/climate").json()
        assert any(s["classification"] == "ABSTAIN" for s in climate["signals"])
        assert climate["cycle"] is None
    finally:
        store.close()


def test_actual_saved_ridge_and_preprocessing_are_used_without_meta_calls(client, app, portfolio, tmp_path):
    from headline_model.features import FeatureCache
    from headline_model.muse_matcher import validate_assessment
    from headline_model.ridge_model import PositionRidge
    from headline_model.paths import MODEL_ROOT
    model = MODEL_ROOT / "models/ridge-v3-full.joblib"
    evidence_model = PositionRidge(model).evidence_model
    cache_path = tmp_path / "evidence.sqlite3"
    cache = FeatureCache(cache_path)
    a = article(headline="Synthetic test: condition officially ruled out")
    try:
        for t in news.watchlist(object_session(portfolio[0]), portfolio[1])["topics"]:
            c = t["contract"]
            row = {"headline": a["headline"], "summary": a["summary"], "published_at": a["published_at"],
                   "market_question": c["question"], "resolution_rules": c["resolution_rules"], "deadline": c["deadline"],
                   "outcome": c["outcome"], "position_thesis": c["thesis"]}
            assessment = validate_assessment({"related": True, "confidence": 0.95,
                "reason": "Synthetic test evidence", "evidence": [a["headline"]], "adverse_evidence": 1.0,
                "favorable_evidence": 0.0, "uncertainty": 0.1, "event_specificity": 0.95, "time_alignment": 0.9}, a, evidence_model)
            cache.put(row, assessment)
    finally:
        cache.close()
    app.state.settings.news_model_path = str(model)
    app.state.settings.news_feature_cache = str(cache_path)
    prefix = f"/baskets/{portfolio[1].id}/climate"
    response = client.post(prefix + "/articles", json=a)
    assert response.status_code == 200, response.text
    assert len(response.json()["saved"]) == 3
    assert response.json()["skipped"] == []
    readings = client.get(prefix).json()["signals"]
    selected = [s for s in readings if s["headline"] == a["headline"]]
    assert len(selected) == 3
    assert all(s["classification"] == "ADVERSE" for s in selected)
    # A different article is never silently scored with invented or reused features.
    absent = client.post(prefix + "/articles", json=article(headline="Uncached synthetic article"))
    assert absent.status_code == 200
    assert absent.json()["saved"] == []
    assert all(s["reason"] == "missing_cached_evidence" for s in absent.json()["skipped"])
