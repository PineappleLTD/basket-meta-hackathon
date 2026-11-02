import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Base, make_engine
from app.main import create_app
from tests.fake_kalshi import FakeKalshi


# Set to a throwaway Postgres database to run the tests against Postgres instead of SQLite.
# Every table in it is dropped before each test.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def app():
    if TEST_DATABASE_URL != "sqlite://":
        engine = make_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(engine)
        engine.dispose()
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        shared_database=":memory:",
        backfill_days=30,
        # Every trade sees the fake's current book; tests that want caching build their own broker.
        paper_quote_cache_seconds=0,
        sync_interval_minutes=0,
        live_refresh_seconds=0,
        _env_file=None,
    )
    return create_app(settings, FakeKalshi(seed=0))


def signup(client, email="user@example.com", password="correct horse battery") -> dict:
    """Create an account and return its auth headers."""
    resp = client.post("/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture
def client(app):
    """Signed in as user@example.com. Pop the Authorization header to act anonymously."""
    with TestClient(app) as c:
        c.headers.update(signup(c))
        yield c


@pytest.fixture
def session(app, client):
    with app.state.sessionmaker() as s:
        yield s
