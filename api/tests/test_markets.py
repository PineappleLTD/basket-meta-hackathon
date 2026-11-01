from tests.fake_kalshi import _FIXTURES


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_seeded_markets_sorted_by_volume(client):
    page = client.get("/markets").json()
    assert page["total"] == len(_FIXTURES)
    volumes = [m["volume"] for m in page["items"]]
    assert volumes == sorted(volumes, reverse=True)


def test_filter_by_query(client):
    items = client.get("/markets", params={"q": "FED"}).json()["items"]
    assert [m["external_id"] for m in items] == ["MOCK-FEDCUT-26DEC"]


def test_get_market_404(client):
    assert client.get("/markets/9999").status_code == 404


def test_sync_upserts_without_duplicating(client):
    report = client.post("/markets/sync").json()
    assert report == {
        "created": 0,
        "updated": len(_FIXTURES),
        "closed": 0,
        "resolved": 0,
        "settled": 0,
        "automated": 0,
    }
    assert client.get("/markets").json()["total"] == len(_FIXTURES)
