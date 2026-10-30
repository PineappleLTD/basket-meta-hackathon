import pytest

from app.llm import LLMError
from app.models import Market
from tests.conftest import signup


def _market_ids(client, n):
    return [m["id"] for m in client.get("/markets", params={"limit": n}).json()["items"]]


def _create(client, positions, name="Test basket"):
    return client.post("/baskets", json={"name": name, "positions": positions})


def test_equal_weight_basket_starts_at_nav_100(client):
    ids = _market_ids(client, 4)
    resp = _create(client, [{"market_id": i} for i in ids])
    assert resp.status_code == 201
    basket = resp.json()
    assert basket["nav"] == pytest.approx(100)
    assert basket["max_drift"] == pytest.approx(0)
    for p in basket["positions"]:
        assert p["target_weight"] == pytest.approx(0.25)
        assert p["value"] == pytest.approx(25)


def test_no_side_uses_inverse_price(client):
    market = client.get("/markets").json()["items"][0]
    basket = _create(client, [{"market_id": market["id"], "side": "no"}]).json()
    assert basket["positions"][0]["price"] == pytest.approx(1 - market["yes_price"])


@pytest.mark.parametrize(
    "weights, error",
    [
        ([0.5, 0.3], "sum to 1"),
        ([0.5, None], "for every position"),
    ],
)
def test_invalid_weights(client, weights, error):
    ids = _market_ids(client, 2)
    resp = _create(client, [{"market_id": i, "weight": w} for i, w in zip(ids, weights)])
    assert resp.status_code == 422
    assert error in resp.json()["detail"]


def test_unknown_market_and_duplicates_rejected(client):
    assert _create(client, [{"market_id": 9999}]).status_code == 422
    i = _market_ids(client, 1)[0]
    assert _create(client, [{"market_id": i}, {"market_id": i}]).status_code == 422


def test_duplicate_name_conflicts(client):
    ids = _market_ids(client, 1)
    assert _create(client, [{"market_id": ids[0]}]).status_code == 201
    assert _create(client, [{"market_id": ids[0]}]).status_code == 409


def test_price_move_causes_drift_and_rebalance_restores_targets(client, session):
    a, b = _market_ids(client, 2)
    basket = _create(client, [{"market_id": a, "weight": 0.5}, {"market_id": b, "weight": 0.5}]).json()

    market = session.get(Market, a)
    market.yes_price /= 2
    session.commit()

    detail = client.get(f"/baskets/{basket['id']}").json()
    # A halves: NAV 25 + 50 = 75, A's weight 25/75 = 1/3, drift 1/6
    assert detail["nav"] == pytest.approx(75)
    assert detail["max_drift"] == pytest.approx(1 / 6)

    plan = client.get(f"/baskets/{basket['id']}/rebalance").json()
    assert plan["needs_rebalance"] is True
    actions = {t["market_id"]: t["action"] for t in plan["trades"]}
    assert actions == {a: "buy", b: "sell"}

    # Higher threshold: same drift no longer needs a rebalance
    assert client.get(f"/baskets/{basket['id']}/rebalance", params={"threshold": 0.25}).json()[
        "needs_rebalance"
    ] is False

    result = client.post(f"/baskets/{basket['id']}/rebalance").json()
    assert result["basket"]["nav"] == pytest.approx(75)
    assert result["basket"]["max_drift"] == pytest.approx(0)
    assert result["basket"]["rebalanced_at"] is not None


def test_rebalance_refuses_near_zero_price(client, session):
    a, b = _market_ids(client, 2)
    basket = _create(client, [{"market_id": a}, {"market_id": b}]).json()
    session.get(Market, a).yes_price = 0.0
    session.commit()
    assert client.post(f"/baskets/{basket['id']}/rebalance").status_code == 409


def test_delete_basket(client):
    basket = _create(client, [{"market_id": _market_ids(client, 1)[0]}]).json()
    assert client.delete(f"/baskets/{basket['id']}").status_code == 204
    assert client.get(f"/baskets/{basket['id']}").status_code == 404
    assert client.get("/baskets").json() == []


class FakeNamer:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete_json(self, system, user, schema):
        self.calls.append(user)
        if self.error:
            raise self.error
        return self.reply


def test_unnamed_basket_saves_as_placeholder_then_is_titled(app, client):
    namer = app.state.namer = FakeNamer({"name": "Rate Cut Bets."})
    ids = _market_ids(client, 2)
    body = {"description": "Rates fall.", "positions": [{"market_id": i} for i in ids]}
    first = client.post("/baskets", json=body).json()
    second = client.post("/baskets", json=body).json()
    assert (first["name"], second["name"]) == ("New basket", "New basket (2)")
    assert namer.calls == []

    assert client.post(f"/baskets/{first['id']}/title").json()["name"] == "Rate Cut Bets"
    assert "Rates fall." in namer.calls[0]
    assert client.post(f"/baskets/{second['id']}/title").json()["name"] == "Rate Cut Bets (2)"
    # Retitling keeps its own name rather than counting it as taken.
    assert client.post(f"/baskets/{first['id']}/title").json()["name"] == "Rate Cut Bets"
    assert client.get(f"/baskets/{first['id']}").json()["name"] == "Rate Cut Bets"


def test_title_falls_back_to_description_then_market(app, client):
    ids = _market_ids(client, 1)
    body = {"description": "Rates fall.", "positions": [{"market_id": ids[0]}]}

    def titled(body):
        return client.post(f"/baskets/{client.post('/baskets', json=body).json()['id']}/title").json()["name"]

    app.state.namer = FakeNamer(error=LLMError("down"))
    assert titled(body) == "Rates fall."
    app.state.namer = None
    assert titled(body) == "Rates fall. (2)"
    question = client.get(f"/markets/{ids[0]}").json()["question"]
    del body["description"]
    assert titled(body) == question


def test_title_is_owner_only(client):
    basket = _create(client, [{"market_id": _market_ids(client, 1)[0]}]).json()
    client.headers.update(signup(client, "other@example.com"))
    assert client.post(f"/baskets/{basket['id']}/title").status_code == 404
