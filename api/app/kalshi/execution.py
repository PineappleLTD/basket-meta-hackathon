"""Authenticated Kalshi V2 transport; writes are never retried automatically.

New orders are IOC. Sells are reduce-only. A timeout is an unknown submission,
not permission to send another order. The durable worker owns recovery.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import base64
import re
import time
from urllib.parse import quote, urlsplit

import httpx

from app.models import Action, Side

ROOTS = {"demo": "https://external-api.demo.kalshi.co", "production": "https://external-api.kalshi.com"}
API = "/trade-api/v2"


class VenueError(ValueError):
    pass


class SubmissionUnknown(VenueError):
    pass


class OrderRejected(VenueError):
    pass


def decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise VenueError("Malformed exchange number") from None
    if not number.is_finite():
        raise VenueError("Non-finite exchange number")
    return number


def whole(value):
    number = decimal(value)
    if number < 0 or number != number.to_integral_value():
        raise VenueError("Fractional or negative contract quantity is unsupported")
    return int(number)


def cents(value):
    number = decimal(value) * 100
    if number < 0 or number != number.to_integral_value():
        raise VenueError("Exchange cash must reconcile to whole cents")
    return int(number)


@dataclass(frozen=True)
class Intent:
    client_id: str
    ticker: str
    side: Side
    action: Action
    count: int
    limit_price: Decimal
    subaccount: int

    def payload(self):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.client_id) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", self.ticker):
            raise VenueError("Invalid exchange identity")
        price = decimal(self.limit_price)
        if type(self.count) is not int or self.count <= 0 or not 0 < price < 1 or not 0 <= self.subaccount <= 63:
            raise VenueError("Invalid order bounds")
        yes_price = price if self.side == Side.YES else 1 - price
        # Conservatively round to four decimal places; never relax the held-side limit.
        bid = (self.side == Side.YES) == (self.action == Action.BUY)
        yes_price = yes_price.quantize(Decimal("0.0001"), rounding=ROUND_FLOOR if bid else ROUND_CEILING)
        if not 0 < yes_price < 1:
            raise VenueError("Order price falls outside exchange bounds")
        return {"ticker": self.ticker, "client_order_id": self.client_id,
                "side": "bid" if bid else "ask", "count": f"{self.count}.00", "price": str(yes_price),
                "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                "reduce_only": self.action == Action.SELL, "cancel_order_on_pause": True,
                "subaccount": self.subaccount}


@dataclass(frozen=True)
class ExecutionFill:
    fill_id: str
    count: int
    price: Decimal
    gross_cents: int
    fee_cents: int


@dataclass(frozen=True)
class Snapshot:
    order_id: str
    status: str
    filled_count: int
    remaining_count: int
    fills: tuple[ExecutionFill, ...]

    @property
    def terminal(self):
        return self.status in {"executed", "canceled", "cancelled"}


class Signer:
    def __init__(self, key_id, private_key):
        from cryptography.hazmat.primitives import serialization
        if not key_id or len(key_id) > 200:
            raise VenueError("A Kalshi key ID is required")
        self.key_id = key_id
        self.key = serialization.load_pem_private_key(private_key, password=None)

    def headers(self, method, path, timestamp):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
        message = f"{timestamp}{method.upper()}{path.split('?')[0]}".encode()
        if isinstance(self.key, ed25519.Ed25519PrivateKey):
            signature = self.key.sign(message)
        elif isinstance(self.key, rsa.RSAPrivateKey):
            signature = self.key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                      salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        else:
            raise VenueError("Unsupported signing key type")
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}


class KalshiExecution:
    def __init__(self, signer, *, environment="demo", subaccount=0, http=None, clock=time.time, page_budget=20):
        if environment not in ROOTS or not 0 <= subaccount <= 63 or not 1 <= page_budget <= 100:
            raise VenueError("Invalid exchange configuration")
        self.environment, self.subaccount, self.signer = environment, subaccount, signer
        self.http = http or httpx.Client(base_url=ROOTS[environment], timeout=20, follow_redirects=False)
        self.clock, self.page_budget = clock, page_budget

    def _request(self, method, path, *, params=None, body=None):
        target = API + path
        try:
            response = self.http.request(method, ROOTS[self.environment] + target, params=params, json=body,
                                         headers=self.signer.headers(method, target, int(self.clock() * 1000)),
                                         follow_redirects=False)
        except (httpx.HTTPError, OSError):
            raise SubmissionUnknown("Exchange request outcome is unknown") from None
        if response.status_code >= 300:
            if method == "POST" and response.status_code in {400, 401, 403, 422}:
                raise OrderRejected(f"Exchange rejected order (HTTP {response.status_code})")
            raise SubmissionUnknown(f"Exchange reconciliation required (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError:
            raise SubmissionUnknown("Exchange response could not be decoded") from None
        if not isinstance(data, dict):
            raise SubmissionUnknown("Exchange response is not an object")
        return data

    def _pages(self, path, key, params):
        cursor, seen = None, set()
        for _ in range(self.page_budget):
            data = self._request("GET", path, params={**params, "limit": 1000, **({"cursor": cursor} if cursor else {})})
            if not isinstance(data.get(key), list):
                raise VenueError("Malformed exchange page")
            yield from data[key]
            cursor = data.get("cursor")
            if not cursor:
                return
            if cursor in seen:
                raise VenueError("Repeated exchange pagination cursor")
            seen.add(cursor)
        raise VenueError("Exchange pagination budget exhausted")

    def submit(self, intent):
        if intent.subaccount != self.subaccount:
            raise VenueError("Wrong exchange subaccount")
        data = self._request("POST", "/portfolio/events/orders", body=intent.payload())
        oid = data.get("order_id")
        if not isinstance(oid, str) or not oid or data.get("client_order_id", intent.client_id) != intent.client_id:
            raise SubmissionUnknown("Exchange order identity is missing or inconsistent")
        return oid  # Immediate response counts are not booked as confirmed fills.

    def reconcile(self, intent, order_id=None):
        try:
            return self._reconcile(intent, order_id)
        except (KeyError, TypeError, AttributeError, IndexError):
            raise VenueError("Malformed exchange order or fill evidence") from None

    def _reconcile(self, intent, order_id=None):
        if order_id:
            order = self._request("GET", "/portfolio/orders/" + quote(order_id, safe=""))["order"]
        else:
            matches = [o for o in self._pages("/portfolio/orders", "orders",
                       {"ticker": intent.ticker, "subaccount": self.subaccount}) if o.get("client_order_id") == intent.client_id]
            if len(matches) != 1:
                raise SubmissionUnknown("Order has not been uniquely located; submission must not be repeated")
            order = matches[0]
        payload = intent.payload()
        if order.get("ticker") != intent.ticker or order.get("client_order_id") != intent.client_id or order.get("subaccount_number") != self.subaccount:
            raise VenueError("Exchange order scope mismatch")
        if order.get("book_side") != payload["side"] or whole(order["initial_count_fp"]) != intent.count:
            raise VenueError("Exchange order direction or size mismatch")
        if order.get("outcome_side") != ("yes" if payload["side"] == "bid" else "no"):
            raise VenueError("Exchange exposure direction mismatch")
        if decimal(order["yes_price_dollars"]) != decimal(payload["price"]):
            raise VenueError("Exchange order price mismatch")
        filled, remaining = whole(order["fill_count_fp"]), whole(order["remaining_count_fp"])
        if filled + remaining > intent.count or order.get("status") not in {"resting", "executed", "canceled", "cancelled"}:
            raise VenueError("Invalid exchange order state")
        fills = []
        for f in self._pages("/portfolio/fills", "fills", {"order_id": order["order_id"], "subaccount": self.subaccount}):
            if f.get("order_id") != order["order_id"] or (f.get("ticker") or f.get("market_ticker")) != intent.ticker or f.get("subaccount_number") != self.subaccount:
                raise VenueError("Exchange fill scope mismatch")
            if f.get("book_side") != payload["side"]:
                raise VenueError("Exchange fill direction mismatch")
            if f.get("outcome_side") != ("yes" if payload["side"] == "bid" else "no"):
                raise VenueError("Exchange fill exposure direction mismatch")
            yes, no = decimal(f["yes_price_dollars"]), decimal(f["no_price_dollars"])
            if yes + no != 1:
                raise VenueError("Exchange fill prices do not complement")
            price = yes if intent.side == Side.YES else no
            if not 0 < price < 1 or (intent.action == Action.BUY and price > intent.limit_price) or (intent.action == Action.SELL and price < intent.limit_price):
                raise VenueError("Exchange fill violates the price limit")
            count = whole(f["count_fp"])
            if count <= 0 or not isinstance(f.get("fill_id"), str) or not 0 < len(f["fill_id"]) <= 200:
                raise VenueError("Invalid individual fill identity or quantity")
            amount = price * count * 100
            # The account cash ledger uses cents; unrepresentable executions require review.
            if amount != amount.to_integral_value():
                raise VenueError("Sub-cent executions require review before cash can be booked")
            gross = int(amount)
            fills.append(ExecutionFill(str(f["fill_id"]), count, price, gross, cents(f["fee_cost"])))
        ids = [f.fill_id for f in fills]
        if len(set(ids)) != len(ids) or sum(f.count for f in fills) != filled:
            raise SubmissionUnknown("Order and individual fills have not reconciled")
        return Snapshot(str(order["order_id"]), order["status"], filled, remaining, tuple(fills))

    def cancel(self, order_id):
        self._request("DELETE", "/portfolio/events/orders/" + quote(order_id, safe=""), params={"subaccount": self.subaccount})

    def account_snapshot(self):
        try:
            return self._account_snapshot()
        except (KeyError, TypeError, AttributeError, IndexError):
            raise VenueError("Malformed exchange balance or position evidence") from None

    def _account_snapshot(self):
        balance = self._request("GET", "/portfolio/balance", params={"subaccount": self.subaccount})
        positions = {}
        for p in self._pages("/portfolio/positions", "market_positions", {"subaccount": self.subaccount}):
            quantity = decimal(p["position_fp"])
            if quantity != quantity.to_integral_value():
                raise VenueError("Fractional external holdings require review")
            if quantity:
                ticker = p["ticker"]
                if not isinstance(ticker, str) or not ticker or ticker in positions:
                    raise VenueError("Invalid or duplicate exchange holding identity")
                positions[ticker] = int(quantity)
        return {"cash_cents": whole(balance["balance"]), "positions": positions}
