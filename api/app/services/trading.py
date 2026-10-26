"""Trading baskets with an account's cash.

Buying a basket splits the amount across its positions by target weight and buys
whole contracts at each side's ask, fees included, without spending more than
the amount. Selling sells a share of every holding at the bid. Rebalancing
trades the holdings back to target weights at their current market value,
selling first so the proceeds fund the buys; legs already within half a point
of target are left alone.

Every order is planned first, from the broker's quotes, and nothing is sent
unless the whole plan is valid. Holdings are marked at the market price (bid/ask
mid, else last trade), the same price baskets are valued at.
"""

import math
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.brokers import Broker, OrderRequest
from app.models import (
    Account,
    AccountMode,
    AccountValue,
    Action,
    Basket,
    BasketOrder,
    BasketOrderKind,
    Holding,
    Market,
    Order,
    Side,
    User,
    utcnow,
)

# Float slack when checking a cost against a budget.
_EPS = 1e-9
# Rebalancing leaves a leg alone when its weight is this close to target, so whole-contract
# rounding and fees don't make every rebalance trade again.
REBALANCE_TOLERANCE = 0.005


class TradingError(ValueError):
    pass


@dataclass(frozen=True)
class SkippedLeg:
    market_id: int
    side: Side
    reason: str


@dataclass
class Plan:
    kind: BasketOrderKind
    amount: float | None
    orders: list[OrderRequest]
    skipped: list[SkippedLeg] = field(default_factory=list)


@dataclass(frozen=True)
class HoldingValuation:
    holding: Holding
    price: float
    value: float
    # Share of the basket's holdings value.
    weight: float
    target_weight: float


@dataclass(frozen=True)
class BasketHoldings:
    basket: Basket
    value: float
    cost: float
    holdings: list[HoldingValuation]


@dataclass(frozen=True)
class AccountValuation:
    account: Account
    holdings_value: float
    baskets: list[BasketHoldings]

    @property
    def equity(self) -> float:
        return self.account.cash + self.holdings_value


def get_account(session: Session, user: User, mode: AccountMode, starting_cash: float) -> Account:
    """The user's account for `mode`, opened with `starting_cash` if they have none."""
    account = session.scalar(select(Account).where(Account.user_id == user.id, Account.mode == mode))
    if account is None:
        account = Account(user_id=user.id, mode=mode, cash=starting_cash, starting_cash=starting_cash)
        session.add_all([account, AccountValue(account=account, ts=utcnow(), equity=starting_cash)])
        session.commit()
    return account


def reset_account(session: Session, account: Account, starting_cash: float) -> None:
    """Drop every holding, order and equity point, and restore the starting cash."""
    account.holdings.clear()
    session.execute(delete(BasketOrder).where(BasketOrder.account_id == account.id))
    session.execute(delete(AccountValue).where(AccountValue.account_id == account.id))
    account.cash = account.starting_cash = starting_cash
    account.reset_at = now = utcnow()
    session.add(AccountValue(account=account, ts=now, equity=starting_cash))


def value_account(account: Account) -> AccountValuation:
    by_basket: dict[int, list[Holding]] = {}
    for h in account.holdings:
        by_basket.setdefault(h.basket_id, []).append(h)

    baskets = []
    for holdings in by_basket.values():
        basket = holdings[0].basket
        targets = {(p.market_id, p.side): p.target_weight for p in basket.positions}
        priced = [(h, h.market.price_for(h.side)) for h in holdings]
        value = sum(h.contracts * price for h, price in priced)
        baskets.append(
            BasketHoldings(
                basket=basket,
                value=value,
                cost=sum(h.cost for h in holdings),
                holdings=[
                    HoldingValuation(
                        holding=h,
                        price=price,
                        value=h.contracts * price,
                        weight=h.contracts * price / value if value > 0 else 0.0,
                        target_weight=targets.get((h.market_id, h.side), 0.0),
                    )
                    for h, price in priced
                ],
            )
        )
    baskets.sort(key=lambda b: b.value, reverse=True)
    return AccountValuation(account, sum(b.value for b in baskets), baskets)


def _holdings(account: Account, basket: Basket) -> dict[tuple[int, Side], Holding]:
    return {(h.market_id, h.side): h for h in account.holdings if h.basket_id == basket.id}


def _quote_all(broker: Broker, legs: list[tuple[Market, Side, Action]]) -> list[float]:
    """Each leg's quote. Raises naming every leg that can't trade."""
    quotes = [broker.quote(m, side, action) for m, side, action in legs]
    missing = [f"{m.id} {side}" for (m, side, _), q in zip(legs, quotes) if q is None]
    if missing:
        raise TradingError(f"No quote to trade against for: {', '.join(missing)}")
    return quotes


def _affordable(broker: Broker, budget: float, price: float) -> int:
    """Most contracts at `price` whose cost plus fee fits in `budget`."""

    def fits(count: int) -> bool:
        return count * price + broker.fee(count, price) <= budget + _EPS

    # Cost rises with count, so binary search the largest count that fits.
    lo, hi = 0, math.floor(budget / price + _EPS)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def plan_buy(account: Account, basket: Basket, amount: float, broker: Broker) -> Plan:
    if amount > account.cash + _EPS:
        raise TradingError(f"Not enough cash: ${account.cash:,.2f} available")
    positions = basket.positions
    quotes = _quote_all(broker, [(p.market, p.side, Action.BUY) for p in positions])
    orders, skipped = [], []
    for p, price in zip(positions, quotes):
        count = _affordable(broker, amount * p.target_weight, price)
        if count:
            orders.append(OrderRequest(p.market, p.side, Action.BUY, count, price))
        else:
            skipped.append(SkippedLeg(p.market_id, p.side, f"${amount * p.target_weight:.2f} buys no contracts"))
    if not orders:
        raise TradingError("Amount is too small to buy a contract of any position")
    return Plan(BasketOrderKind.BUY, amount, orders, skipped)


def plan_sell(account: Account, basket: Basket, fraction: float, broker: Broker) -> Plan:
    """Sell `fraction` of every holding, rounded down. A leg with no bid is left held."""
    holdings = list(_holdings(account, basket).values())
    if not holdings:
        raise TradingError("No holdings in this basket")
    orders, skipped = [], []
    for h in holdings:
        count = h.contracts if fraction >= 1 else math.floor(h.contracts * fraction)
        if count == 0:
            continue
        price = broker.quote(h.market, h.side, Action.SELL)
        if price is None:
            reason = "market is closed" if not h.market.is_open else "no bid"
            skipped.append(SkippedLeg(h.market_id, h.side, reason))
        else:
            orders.append(OrderRequest(h.market, h.side, Action.SELL, count, price))
    if not orders:
        reasons = "; ".join(f"{s.market_id} {s.reason}" for s in skipped)
        raise TradingError(f"Nothing to sell: {reasons or 'every holding rounds down to 0 contracts'}")
    return Plan(BasketOrderKind.SELL, fraction, orders, skipped)


def plan_rebalance(account: Account, basket: Basket, broker: Broker) -> Plan:
    held = _holdings(account, basket)
    if not held:
        raise TradingError("No holdings in this basket")
    value = sum(h.contracts * h.market.price_for(h.side) for h in held.values())

    deltas = []
    for p in basket.positions:
        mark = p.market.price_for(p.side)
        current = held[(p.market_id, p.side)].contracts if (p.market_id, p.side) in held else 0
        if value <= 0 or abs(current * mark / value - p.target_weight) < REBALANCE_TOLERANCE:
            continue
        target = math.floor(value * p.target_weight / mark + _EPS) if mark > 0 else 0
        if target != current:
            deltas.append((p.market, p.side, target - current))
    if not deltas:
        raise TradingError("Holdings are already at target weights")

    legs = [(m, side, Action.BUY if d > 0 else Action.SELL) for m, side, d in deltas]
    quotes = _quote_all(broker, legs)
    orders = [
        OrderRequest(m, side, action, abs(d), price)
        for (m, side, d), (_, _, action), price in zip(deltas, legs, quotes)
    ]
    # Sells first, so their proceeds are in cash before the buys.
    orders.sort(key=lambda o: o.action != Action.SELL)
    if account.cash + cash_delta(orders, broker) < -_EPS:
        raise TradingError(f"Not enough cash to rebalance: ${account.cash:,.2f} available")
    return Plan(BasketOrderKind.REBALANCE, None, orders)


def order_cash(order: OrderRequest, broker: Broker) -> float:
    """Expected cash change from filling `order` at its quote: negative for a buy."""
    gross = order.count * order.limit_price
    fee = broker.fee(order.count, order.limit_price)
    return -(gross + fee) if order.action == Action.BUY else gross - fee


def cash_delta(orders: list[OrderRequest], broker: Broker) -> float:
    return sum(order_cash(o, broker) for o in orders)


def execute(session: Session, account: Account, basket: Basket, plan: Plan, broker: Broker) -> BasketOrder:
    """Send the plan's orders in order, and book each fill to cash and holdings."""
    record = BasketOrder(
        account_id=account.id, basket_id=basket.id, basket_name=basket.name, kind=plan.kind, amount=plan.amount
    )
    held = _holdings(account, basket)
    for request in plan.orders:
        fill = broker.execute(request)
        order = Order(
            market=request.market,
            side=request.side,
            action=request.action,
            count=request.count,
            limit_price=request.limit_price,
            filled_count=fill.count,
            fill_price=fill.price,
            fee=fill.fee,
            realized_pnl=None,
        )
        record.orders.append(order)
        if fill.count:
            _book(account, basket, held, order)

    session.add_all([record, AccountValue(account=account, ts=utcnow(), equity=value_account(account).equity)])
    return record


def _book(account: Account, basket: Basket, held: dict[tuple[int, Side], Holding], order: Order) -> None:
    key = (order.market.id, order.side)
    gross = order.filled_count * order.fill_price
    if order.action == Action.BUY:
        holding = held.get(key)
        if holding is None:
            holding = held[key] = Holding(basket=basket, market=order.market, side=order.side, contracts=0, cost=0.0)
            account.holdings.append(holding)
        holding.contracts += order.filled_count
        holding.cost += gross + order.fee
        account.cash -= gross + order.fee
        return

    holding = held[key]
    basis = holding.cost * order.filled_count / holding.contracts
    proceeds = gross - order.fee
    order.realized_pnl = proceeds - basis
    holding.contracts -= order.filled_count
    holding.cost -= basis
    account.cash += proceeds
    if holding.contracts == 0:
        account.holdings.remove(holding)
        del held[key]
