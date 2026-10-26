from datetime import timedelta

import pytest

from app.auth import hash_password, verify_password
from app.models import AuthSession, User, utcnow
from tests.conftest import signup


def _market_id(client):
    return client.get("/markets").json()["items"][0]["id"]


def test_password_hashing_round_trips_and_salts():
    stored = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", stored)
    assert not verify_password("hunter2hunter3", stored)
    assert hash_password("hunter2hunter2") != stored
    assert not verify_password("anything", "garbage")


def test_signup_login_me_logout(client):
    client.headers.pop("Authorization")
    resp = client.post("/auth/signup", json={"email": "  Ada@Example.COM ", "password": "longenough"})
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "ada@example.com"
    signup_token = resp.json()["token"]

    # Email matching is case-insensitive, for both duplicate checks and sign-in.
    assert client.post("/auth/signup", json={"email": "ADA@example.com", "password": "longenough"}).status_code == 409
    login = client.post("/auth/login", json={"email": "Ada@example.com", "password": "longenough"})
    assert login.status_code == 200
    token = login.json()["token"]
    assert token != signup_token

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json()["email"] == "ada@example.com"

    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401
    # Logging out one session leaves the others signed in.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {signup_token}"}).status_code == 200


@pytest.mark.parametrize(
    "email, password",
    [("user@example.com", "wrong password"), ("nobody@example.com", "correct horse battery")],
)
def test_bad_credentials_get_the_same_answer(client, email, password):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Wrong email or password"


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": "longenough"},
        {"email": "a@b", "password": "longenough"},
        {"email": "a@example.com", "password": "short"},
    ],
)
def test_signup_validation(client, body):
    assert client.post("/auth/signup", json=body).status_code == 422


def test_passwords_and_tokens_are_not_stored_in_plain_text(client, session):
    user = session.query(User).one()
    assert "correct horse battery" not in user.password_hash
    token = client.headers["Authorization"].removeprefix("Bearer ")
    assert all(token != s.token_hash for s in session.query(AuthSession))


def test_expired_session_is_rejected(client, session):
    for s in session.query(AuthSession):
        s.expires_at = utcnow() - timedelta(seconds=1)
    session.commit()
    resp = client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_protected_routes_need_a_token(client):
    client.headers.pop("Authorization")
    assert client.get("/markets").status_code == 200
    assert client.get("/baskets").status_code == 401
    assert client.post("/baskets", json={"name": "x", "positions": [{"market_id": 1}]}).status_code == 401
    assert client.post("/theses/select", json={"thesis": "Rates fall"}).status_code == 401
    assert client.post("/markets/sync").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_users_only_see_their_own_baskets(client):
    mine = client.post("/baskets", json={"name": "Shared name", "positions": [{"market_id": _market_id(client)}]})
    assert mine.status_code == 201
    basket_id = mine.json()["id"]

    other = signup(client, email="other@example.com")
    assert client.get("/baskets", headers=other).json() == []
    for method, path in [
        ("get", f"/baskets/{basket_id}"),
        ("get", f"/baskets/{basket_id}/nav"),
        ("get", f"/baskets/{basket_id}/rebalance"),
        ("post", f"/baskets/{basket_id}/rebalance"),
        ("delete", f"/baskets/{basket_id}"),
    ]:
        assert getattr(client, method)(path, headers=other).status_code == 404, path

    # Names only need to be unique per user.
    theirs = client.post(
        "/baskets", json={"name": "Shared name", "positions": [{"market_id": _market_id(client)}]}, headers=other
    )
    assert theirs.status_code == 201
    assert [b["id"] for b in client.get("/baskets").json()] == [basket_id]
    assert client.get(f"/baskets/{basket_id}").status_code == 200
