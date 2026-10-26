"""Where orders go: simulated fills for paper accounts, an exchange for live ones.

Contracts are whole numbers and pay $1 if their side wins, so a price is dollars
per contract. On Kalshi's book a NO bid is a YES ask at 1 - price, so the NO
quotes come from the YES ones.
"""

import math
from dataclasses import dataclass
from typing import Protocol

from app.models import Action, Market, Side


@dataclass(frozen=True)
class OrderRequest:
    market: Market
    side: Side
    action: Action
    count: int
    # Quote the order was sized against. A live broker sends it as the limit.
    limit_price: float


@dataclass(frozen=True)
class Fill:
    count: int
    # Average price per contract; None when nothing filled.
    price: float | None
    fee: float


class Broker(Protocol):
    def quote(self, market: Market, side: Side, action: Action) -> float | None:
        """Price `action` would trade one contract at, or None if it can't trade now."""
        ...

    def fee(self, count: int, price: float) -> float:
        """Fee for filling `count` contracts at `price`."""
        ...

    def execute(self, order: OrderRequest) -> Fill: ...


def kalshi_fee(count: int, price: float, rate: float) -> float:
    """Kalshi's trading fee: rate * contracts * price * (1 - price), rounded up to the cent."""
    cents = rate * count * price * (1 - price) * 100
    # Rounded first so float error like 7.0000000001 doesn't add a cent.
    return math.ceil(round(cents, 6)) / 100


def top_of_book(market: Market, side: Side, action: Action) -> float | None:
    """Best price on the market's stored book for buying (the ask) or selling (the bid) `side`."""
    if side == Side.YES:
        return market.yes_ask if action == Action.BUY else market.yes_bid
    yes = market.yes_bid if action == Action.BUY else market.yes_ask
    return None if yes is None else 1.0 - yes


class PaperBroker:
    """Fills every order in full at the stored top of book, as a taker.

    Quotes are as fresh as the last sync, and the book's depth isn't known, so
    size never moves the price. Orders in closed markets, or on a side with no
    quote, don't fill.
    """

    def __init__(self, fee_rate: float):
        self.fee_rate = fee_rate

    def quote(self, market: Market, side: Side, action: Action) -> float | None:
        if not market.is_open:
            return None
        price = top_of_book(market, side, action)
        return price if price is not None and 0 < price < 1 else None

    def fee(self, count: int, price: float) -> float:
        return kalshi_fee(count, price, self.fee_rate)

    def execute(self, order: OrderRequest) -> Fill:
        price = self.quote(order.market, order.side, order.action)
        crossed = price is not None and (
            price <= order.limit_price if order.action == Action.BUY else price >= order.limit_price
        )
        if not crossed or order.count <= 0:
            return Fill(count=0, price=None, fee=0.0)
        return Fill(count=order.count, price=price, fee=self.fee(order.count, price))
