"""Basket (index) math.

A basket is a set of market outcomes with target weights. At creation (and
after each rebalance) units are set so that each position's value equals
target_weight * NAV. Prices then move, values drift away from target, and a
rebalance trades units back to target at the current NAV.

A position whose market has closed keeps its last price, and once the market
resolves, its payout, so it sits in the basket like cash and NAV doesn't jump.
The next rebalance takes it out and spreads its weight over the open positions.
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Basket, BasketPosition, Market, utcnow
from app.schemas import PositionIn

BASE_NAV = 100.0
# Positions can't be sized against a price this close to 0: units would explode.
MIN_PRICE = 0.01
WEIGHT_TOLERANCE = 1e-4
DEFAULT_DRIFT_THRESHOLD = 0.05


class BasketError(ValueError):
    pass


@dataclass(frozen=True)
class PositionValuation:
    position: BasketPosition
    price: float
    value: float
    current_weight: float
    drift: float


@dataclass(frozen=True)
class Valuation:
    nav: float
    positions: list[PositionValuation]

    @property
    def max_drift(self) -> float:
        return max((abs(p.drift) for p in self.positions), default=0.0)


@dataclass(frozen=True)
class Trade:
    position: BasketPosition
    price: float
    current_units: float
    target_units: float

    @property
    def delta_units(self) -> float:
        return self.target_units - self.current_units

    @property
    def action(self) -> str:
        if math.isclose(self.current_units, self.target_units, rel_tol=1e-9, abs_tol=1e-9):
            return "hold"
        return "buy" if self.delta_units > 0 else "sell"


def resolve_weights(weights: list[float | None]) -> list[float]:
    """All None -> equal weight. Otherwise every weight is required and must sum to 1."""
    if all(w is None for w in weights):
        return [1.0 / len(weights)] * len(weights)
    if any(w is None for w in weights):
        raise BasketError("Give a weight for every position, or for none (equal weight)")
    total = sum(weights)
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise BasketError(f"Weights must sum to 1, got {total:.6f}")
    return [w / total for w in weights]


def _target_units(position: BasketPosition, nav: float, weight: float) -> float:
    price = position.market.price_for(position.side)
    if price < MIN_PRICE:
        raise BasketError(
            f"Market {position.market.id} {position.side} price {price:.4f} is too low to size a position"
        )
    return nav * weight / price


def rebalanced_weights(basket: Basket) -> dict[int, float]:
    """Target weight per position id after a rebalance: open positions' targets scaled to sum to 1."""
    open_positions = [p for p in basket.positions if p.market.is_open]
    if not open_positions:
        raise BasketError("Every market in this basket has closed")
    total = sum(p.target_weight for p in open_positions)
    return {p.id: p.target_weight / total for p in open_positions}


def build_positions(session: Session, specs: list[PositionIn]) -> list[BasketPosition]:
    keys = [(s.market_id, s.side) for s in specs]
    if len(set(keys)) != len(keys):
        raise BasketError("Duplicate market/side in positions")

    ids = {s.market_id for s in specs}
    markets = {m.id: m for m in session.scalars(select(Market).where(Market.id.in_(ids)))}
    missing = sorted(ids - markets.keys())
    if missing:
        raise BasketError(f"Unknown market ids: {missing}")
    closed = sorted(i for i in ids if not markets[i].is_open)
    if closed:
        raise BasketError(f"Markets are closed: {closed}")

    weights = resolve_weights([s.weight for s in specs])
    positions = [
        BasketPosition(market=markets[s.market_id], side=s.side, target_weight=w)
        for s, w in zip(specs, weights)
    ]
    for p in positions:
        p.units = _target_units(p, BASE_NAV, p.target_weight)
    return positions


def value_basket(basket: Basket) -> Valuation:
    priced = [(p, p.market.price_for(p.side)) for p in basket.positions]
    nav = sum(p.units * price for p, price in priced)
    positions = []
    for p, price in priced:
        value = p.units * price
        weight = value / nav if nav > 0 else 0.0
        positions.append(PositionValuation(p, price, value, weight, weight - p.target_weight))
    return Valuation(nav=nav, positions=positions)


def plan_rebalance(basket: Basket) -> tuple[Valuation, list[Trade]]:
    """Closed positions go to 0 units; open ones to their share of NAV at the rescaled targets."""
    valuation = value_basket(basket)
    weights = rebalanced_weights(basket)
    trades = [
        Trade(
            position=pv.position,
            price=pv.price,
            current_units=pv.position.units,
            target_units=(
                _target_units(pv.position, valuation.nav, weights[pv.position.id])
                if pv.position.id in weights
                else 0.0
            ),
        )
        for pv in valuation.positions
    ]
    return valuation, trades


def apply_rebalance(basket: Basket) -> list[Trade]:
    """Reset units to target, dropping closed positions and rescaling the rest's target weights."""
    weights = rebalanced_weights(basket)
    _, trades = plan_rebalance(basket)
    for trade in trades:
        trade.position.units = trade.target_units
    for p in list(basket.positions):
        if p.id in weights:
            p.target_weight = weights[p.id]
        else:
            basket.positions.remove(p)
    basket.rebalanced_at = utcnow()
    return trades
