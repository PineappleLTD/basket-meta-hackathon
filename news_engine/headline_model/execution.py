"""Execution boundary and paper execution. There is intentionally no live sender.

An ETF backend supplies a broker implementing this interface, including durable
idempotency keyed by action_id, fresh positions/quotes, and fill reconciliation.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PositionState:
    platform: str
    market_id: str
    outcome: str
    quantity: Decimal
    best_bid: Decimal
    as_of: datetime
    tradable: bool = True
    has_open_orders: bool = False


@dataclass(frozen=True)
class Fill:
    status: str
    filled_quantity: Decimal
    average_price: Decimal


class Broker(Protocol):
    def position(self, position_id: str) -> PositionState: ...

    def sell_held_outcome(self, position_id: str, quantity: Decimal, min_price: Decimal, action_id: str) -> Fill:
        """Must be idempotent by action_id and must never increase/flip exposure."""
        ...


def execute(decision, broker: Broker, min_price, now=None):
    if decision['action'] not in {'REDUCE_WEIGHT', 'EXIT'} or 'intent' not in decision:
        raise ValueError('No actionable sell intent.')
    now = now or datetime.now(timezone.utc)
    state = broker.position(decision['position_id'])
    quantity = Decimal(decision['intent']['quantity'])
    floor = Decimal(str(min_price))
    if not floor.is_finite() or not 0 < floor < 1:
        raise ValueError('Choose an explicit minimum sale price between 0 and 1.')
    if (state.platform, state.market_id, state.outcome) != (decision['platform'], decision['market_id'], decision['outcome']):
        raise ValueError('Broker position identity/held outcome changed; re-evaluate.')
    if state.as_of.tzinfo is None or not 0 <= (now-state.as_of).total_seconds() <= 10:
        raise ValueError('Refresh the position and quote before execution.')
    if not state.tradable or state.has_open_orders:
        raise ValueError('Market unavailable or existing orders need reconciliation.')
    if not quantity.is_finite() or not 0 < quantity <= state.quantity or state.quantity != Decimal(decision['intent']['snapshot_quantity']):
        raise ValueError('Position quantity changed; re-evaluate instead of overselling.')
    if not state.best_bid.is_finite() or state.best_bid < floor:
        raise ValueError('No acceptable bid; no exit is guaranteed.')
    fill = broker.sell_held_outcome(decision['position_id'], quantity, floor, decision['action_id'])
    if not fill.filled_quantity.is_finite() or not 0 <= fill.filled_quantity <= quantity:
        raise ValueError('Invalid broker fill; reconcile before retrying.')
    return {
        'action_id': decision['action_id'], 'status': fill.status,
        'filled_quantity': str(fill.filled_quantity), 'unfilled_quantity': str(quantity-fill.filled_quantity),
        'fully_filled': fill.filled_quantity == quantity,
        'note': 'Recompute actual basket weights from reconciled fills and fresh prices.',
    }


class PaperBroker:
    """Local simulation only; keeps immutable trade receipts per action ID."""
    def __init__(self, positions, available_liquidity=None):
        self.positions = dict(positions)
        self.available_liquidity = available_liquidity
        self.receipts = {}

    def position(self, position_id):
        return self.positions[position_id]

    def sell_held_outcome(self, position_id, quantity, min_price, action_id):
        if action_id in self.receipts:
            return self.receipts[action_id]
        state = self.positions[position_id]
        if quantity > state.quantity or state.best_bid < min_price:
            raise ValueError('Paper order would exceed holdings or breach the price floor.')
        filled = min(quantity, self.available_liquidity) if self.available_liquidity is not None else quantity
        receipt = Fill('PAPER_FILLED' if filled == quantity else 'PAPER_PARTIAL', filled, state.best_bid)
        self.receipts[action_id] = receipt
        self.positions[position_id] = PositionState(state.platform, state.market_id, state.outcome, state.quantity-filled, state.best_bid, state.as_of, state.tradable, state.has_open_orders)
        return receipt
