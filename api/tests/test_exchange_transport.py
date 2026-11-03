import base64
from decimal import Decimal
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from app.kalshi.execution import Intent, KalshiExecution, Signer, SubmissionUnknown, VenueError
from app.models import Action, Side


class DummySigner:
    def headers(self, method, path, timestamp):
        return {"KALSHI-ACCESS-KEY": "synthetic-key", "KALSHI-ACCESS-SIGNATURE": "synthetic-signature",
                "KALSHI-ACCESS-TIMESTAMP": str(timestamp)}


@pytest.mark.parametrize("positions", [
    [{"ticker": "TEST", "position_fp": "1.5"}],
    [{"ticker": "TEST", "position_fp": "1"}, {"ticker": "TEST", "position_fp": "2"}],
    [{"ticker": "TEST"}],
])
def test_unusable_account_evidence_cannot_become_a_trading_baseline(positions):
    def handler(request):
        return httpx.Response(200, json={"balance": 1000} if request.url.path.endswith("/balance")
                              else {"market_positions": positions})
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(VenueError):
        venue.account_snapshot()


def test_non_object_submission_response_remains_unknown():
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[]))))
    with pytest.raises(SubmissionUnknown):
        venue.submit(Intent("stable-id", "TEST", Side.YES, Action.SELL, 100, Decimal("0.5"), 0))


@pytest.mark.parametrize("side,action,book,price,reduce", [
    (Side.YES, Action.BUY, "bid", "0.4000", False),
    (Side.YES, Action.SELL, "ask", "0.4000", True),
    (Side.NO, Action.BUY, "ask", "0.6000", False),
    (Side.NO, Action.SELL, "bid", "0.6000", True),
])
def test_v2_side_mapping_and_ioc_reduce_only(side, action, book, price, reduce):
    request = Intent("stable-id", "EXAMPLE-YES", side, action, 100, Decimal("0.4"), 2)
    body = request.payload()
    assert body["side"] == book and body["price"] == price
    assert body["reduce_only"] is reduce
    assert body["time_in_force"] == "immediate_or_cancel"
    assert body["subaccount"] == 2


@pytest.mark.parametrize("algorithm", ["rsa", "ed25519"])
def test_authenticated_signatures_include_full_path_and_exclude_query(algorithm):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if algorithm == "rsa" else ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = Signer("test-key-id", pem)
    headers = signer.headers("get", "/trade-api/v2/portfolio/orders?limit=1", 1234)
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = b"1234GET/trade-api/v2/portfolio/orders"
    if algorithm == "rsa":
        key.public_key().verify(signature, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    else:
        key.public_key().verify(signature, message)


def test_submit_timeout_is_one_request_and_never_automatically_retried():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic timeout")
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SubmissionUnknown):
        venue.submit(Intent("stable-id", "TEST", Side.YES, Action.SELL, 100, Decimal("0.5"), 0))
    assert len(calls) == 1
    assert calls[0].url.host == "external-api.demo.kalshi.co"


def order_data(intent, **changes):
    body = intent.payload()
    return {"order_id": "venue-order", "client_order_id": intent.client_id, "ticker": intent.ticker,
            "subaccount_number": intent.subaccount, "book_side": body["side"],
            "outcome_side": "yes" if body["side"] == "bid" else "no", "yes_price_dollars": body["price"],
            "initial_count_fp": "100.00", "fill_count_fp": "40.00", "remaining_count_fp": "0.00",
            "status": "canceled", **changes}


def fill_data(intent, **changes):
    body = intent.payload()
    return {"fill_id": "fill-1", "order_id": "venue-order", "ticker": intent.ticker,
            "subaccount_number": intent.subaccount, "book_side": body["side"],
            "outcome_side": "yes" if body["side"] == "bid" else "no", "count_fp": "40.00",
            "yes_price_dollars": "0.5", "no_price_dollars": "0.5", "fee_cost": "0.70", **changes}


def test_recovery_finds_client_id_across_pages_and_reconciles_individual_fills():
    intent = Intent("stable-id", "TEST", Side.NO, Action.SELL, 100, Decimal("0.5"), 0)
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/orders"):
            if not request.url.params.get("cursor"):
                return httpx.Response(200, json={"orders": [], "cursor": "next"})
            return httpx.Response(200, json={"orders": [order_data(intent)], "cursor": ""})
        return httpx.Response(200, json={"fills": [fill_data(intent)], "cursor": ""})
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    state = venue.reconcile(intent)
    assert state.terminal and state.filled_count == 40
    assert state.fills[0].gross_cents == 2000 and state.fills[0].fee_cents == 70
    assert len(calls) == 3 and all(r.method == "GET" for r in calls)


@pytest.mark.parametrize("change", [{"subaccount_number": 1}, {"ticker": "OUTSIDE"}, {"book_side": "ask"},
                                    {"outcome_side": "no"}, {"count_fp": "40.5"}, {"yes_price_dollars": "0.7"}])
def test_fill_scope_and_numbers_cannot_be_substituted(change):
    intent = Intent("stable-id", "TEST", Side.NO, Action.SELL, 100, Decimal("0.5"), 0)
    def handler(request):
        if "/orders/" in request.url.path:
            return httpx.Response(200, json={"order": order_data(intent)})
        return httpx.Response(200, json={"fills": [fill_data(intent, **change)], "cursor": ""})
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(VenueError):
        venue.reconcile(intent, "venue-order")


def test_lagging_fill_feed_does_not_release_proceeds():
    intent = Intent("stable-id", "TEST", Side.NO, Action.SELL, 100, Decimal("0.5"), 0)
    def handler(request):
        return httpx.Response(200, json={"order": order_data(intent)}) if "/orders/" in request.url.path else httpx.Response(200, json={"fills": [], "cursor": ""})
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SubmissionUnknown):
        venue.reconcile(intent, "venue-order")


def test_redirect_does_not_forward_credentials_or_resubmit():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://other.example/collect"})
    venue = KalshiExecution(DummySigner(), http=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(SubmissionUnknown):
        venue.submit(Intent("stable-id", "TEST", Side.YES, Action.BUY, 1, Decimal("0.5"), 0))
    assert len(seen) == 1
