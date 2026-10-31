"""Trading baskets with an account's cash.

Holdings track the basket's index. A buy splits the amount by the index's current
weights (what its NAV is made of right now, not the targets it has drifted from),
so the holding moves with NAV. Each leg buys the most whole contracts its share
affords walking the book, fees included; leftover cash then buys one more contract
for the legs furthest under their share, without spending more than the amount.
Selling sells a share of every holding at the bids. Rebalancing resets the index to
its target weights (dropping closed markets) and trades the holdings to match,
selling first so the proceeds fund the buys; legs already within half a point of
target are left alone, as are holdings in closed markets, which wait to settle.

Every order is planned from the broker's books before anything is sent. Legs that
can't trade (closed, no bids, a stale quote) are skipped and reported, and the rest
go ahead. Resolved markets pay their holdings out to cash on the next sync.

Holdings are marked at the market price (bid/ask mid, else last trade, or the
payout once resolved), the same price baskets are valued at. Liquidation value is
what selling everything at the stored best bids would bring after fees.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.brokers import Book, Broker, NoQuote, OrderRequest, Quote, gross_cents, to_cents, top_of_book
from app.models import (
    Account,
    AccountMode,
    AccountValue,
    Action,
    Basket,
    BasketOrder,
    BasketOrderKind,
    ConditionalOrder,
    Holding,
    Market,
    Order,
    OrderOrigin,
    Side,
    Stake,
    StakeValue,
    User,
    utcnow,
)
from app.services.baskets import (
    DEFAULT_DRIFT_THRESHOLD,
    BasketError,
    apply_rebalance,
    rebalanced_weights,
    value_basket,
)

# Rebalancing leaves a leg alone when its weight is this close to target, so whole-contract
# rounding and fees don't make every rebalance trade again.
REBALANCE_TOLERANCE = 0.005

LegKey = tuple[int, Side]


class TradingError(ValueError):
    pass


@dataclass(frozen=True)
class SkippedLeg:
    market_id: int
    side: Side
    reason: str


@dataclass(frozen=True)
class PlannedOrder:
    request: OrderRequest
    # Price of the first and the average contract, so the walk down the book shows.
    best_price: float
    avg_price: float
    gross_cents: int
    fee_cents: int

    @property
    def cash_cents(self) -> int:
        """Cash change from filling as planned: negative for a buy."""
        if self.request.action == Action.BUY:
            return -(self.gross_cents + self.fee_cents)
        return self.gross_cents - self.fee_cents


@dataclass
class Plan:
    kind: BasketOrderKind
    amount: float | None
    orders: list[PlannedOrder]
    skipped: list[SkippedLeg] = field(default_factory=list)
    # A rebalance also resets the basket's index to its targets, so later buys mirror the holdings.
    reset_index: bool = False

    @property
    def cash_delta_cents(self) -> int:
        return sum(o.cash_cents for o in self.orders)


@dataclass(frozen=True)
class HoldingValuation:
    holding: Holding
    price: float
    value: float
    liquidation_value: float
    # Share of the basket's holdings value, and the share a rebalance would bring it to.
    weight: float
    target_weight: float


@dataclass(frozen=True)
class BasketHoldings:
    basket: Basket
    stake: Stake | None
    nav: float
    value: float
    liquidation_value: float
    cost: float
    holdings: list[HoldingValuation]

    @property
    def share_value(self) -> float | None:
        """The holding's value per share, comparable to the basket's NAV."""
        return self.value / self.stake.shares if self.stake and self.stake.shares > 0 else None

    @property
    def max_drift(self) -> float:
        return max((abs(h.weight - h.target_weight) for h in self.holdings), default=0.0)


@dataclass(frozen=True)
class AccountValuation:
    account: Account
    holdings_value: float
    liquidation_value: float
    baskets: list[BasketHoldings]

    @property
    def equity(self) -> float:
        return self.account.cash + self.holdings_value

    @property
    def liquidation_equity(self) -> float:
        return self.account.cash + self.liquidation_value

    @property
    def realized_pnl(self) -> float:
        return sum(s.realized_pnl for s in self.account.stakes)


def get_account(session: Session, user: User, mode: AccountMode, starting_cash: float) -> Account:
    """The user's account for `mode`, opened with `starting_cash` if they have none."""
    account = session.scalar(select(Account).where(Account.user_id == user.id, Account.mode == mode))
    if account is None:
        cents = to_cents(starting_cash, up=False)
        account = Account(user_id=user.id, mode=mode, cash_cents=cents, starting_cash_cents=cents)
        session.add_all([account, AccountValue(account=account, ts=utcnow(), equity=cents / 100)])
        session.commit()
    return account


def reset_account(session: Session, account: Account, starting_cash: float) -> None:
    """Drop every holding, stake, order and equity point, and restore the starting cash."""
    account.holdings.clear()
    account.stakes.clear()
    session.execute(delete(ConditionalOrder).where(ConditionalOrder.account_id == account.id))
    session.execute(delete(BasketOrder).where(BasketOrder.account_id == account.id))
    session.execute(delete(AccountValue).where(AccountValue.account_id == account.id))
    account.cash_cents = account.starting_cash_cents = to_cents(starting_cash, up=False)
    account.reset_at = now = utcnow()
    session.add(AccountValue(account=account, ts=now, equity=account.cash))


def get_stake(account: Account, basket: Basket) -> Stake:
    for stake in account.stakes:
        if stake.basket_id == basket.id:
            return stake
    stake = Stake(basket=basket, basket_id=basket.id, shares=0.0)
    account.stakes.append(stake)
    return stake


def find_stake(account: Account, basket_id: int) -> Stake | None:
    return next((s for s in account.stakes if s.basket_id == basket_id), None)


def _holdings(account: Account, basket: Basket) -> dict[LegKey, Holding]:
    return {(h.market_id, h.side): h for h in account.holdings if h.basket_id == basket.id}


def _mark(holding: Holding) -> float:
    return holding.contracts * holding.market.price_for(holding.side)


def _liquidation(holding: Holding, broker: Broker | None) -> float:
    """Selling at the stored best bid, after fees. A closed market is worth its mark until it pays out."""
    if not holding.market.is_open:
        return _mark(holding)
    bid = top_of_book(holding.market, holding.side, Action.SELL)
    if not bid or bid <= 0:
        return 0.0
    fills = [(bid, float(holding.contracts))]
    fee = broker.fee_cents(fills) if broker else 0
    return (gross_cents(fills, Action.SELL) - fee) / 100


def target_weights(basket: Basket, held: dict[LegKey, Holding]) -> dict[LegKey, float]:
    """The weight each leg would have after a rebalance.

    Holdings in closed markets can't trade, so they keep their weight; open target legs share
    the rest by the basket's rescaled targets, and open legs the basket dropped go to 0.
    """
    values = {key: _mark(h) for key, h in held.items()}
    total = sum(values.values())
    if total <= 0:
        return {key: 0.0 for key in held}
    try:
        weights = rebalanced_weights(basket)
    except BasketError:
        # Nothing left to trade into: every leg stays where it is.
        return {key: v / total for key, v in values.items()}

    targets = {key: 0.0 for key in held}
    frozen = 0.0
    for key, h in held.items():
        if not h.market.is_open:
            targets[key] = values[key] / total
            frozen += targets[key]
    for p in basket.positions:
        if p.id in weights:
            targets[(p.market_id, p.side)] = weights[p.id] * (1 - frozen)
    return targets


def value_basket_holdings(account: Account, basket: Basket, broker: Broker | None = None) -> BasketHoldings:
    held = _holdings(account, basket)
    targets = target_weights(basket, held)
    marks = {key: _mark(h) for key, h in held.items()}
    value = sum(marks.values())
    holdings = [
        HoldingValuation(
            holding=h,
            price=h.market.price_for(h.side),
            value=marks[key],
            liquidation_value=_liquidation(h, broker),
            weight=marks[key] / value if value > 0 else 0.0,
            target_weight=targets.get(key, 0.0),
        )
        for key, h in held.items()
    ]
    return BasketHoldings(
        basket=basket,
        stake=find_stake(account, basket.id),
        nav=value_basket(basket).nav,
        value=value,
        liquidation_value=sum(h.liquidation_value for h in holdings),
        cost=sum(h.cost for h in held.values()),
        holdings=holdings,
    )


def value_account(account: Account, broker: Broker | None = None) -> AccountValuation:
    """Holdings by basket, largest first. Without a broker, liquidation value ignores fees."""
    baskets_by_id = {h.basket_id: h.basket for h in account.holdings}
    baskets = sorted(
        (value_basket_holdings(account, b, broker) for b in baskets_by_id.values()), key=lambda b: b.value, reverse=True
    )
    return AccountValuation(
        account,
        holdings_value=sum(b.value for b in baskets),
        liquidation_value=sum(b.liquidation_value for b in baskets),
        baskets=baskets,
    )


def _planned(market: Market, side: Side, book: Book, fills: list[tuple[float, float]], broker: Broker) -> PlannedOrder:
    count = round(sum(q for _, q in fills))
    return PlannedOrder(
        request=OrderRequest(market, side, book.action, count, limit_price=fills[-1][0]),
        best_price=fills[0][0],
        avg_price=sum(p * q for p, q in fills) / count,
        gross_cents=gross_cents(fills, book.action),
        fee_cents=broker.fee_cents(fills),
    )


@dataclass
class _BuyLeg:
    market: Market
    side: Side
    book: Book
    # Cents this leg should get.
    target: int
    count: int = 0

    def cost(self, broker: Broker, count: int) -> int | None:
        """Cents to buy `count` contracts walking the book, fee included; None if the book is too thin."""
        if count == 0:
            return 0
        fills = self.book.take(count)
        if round(sum(q for _, q in fills)) < count:
            return None
        return gross_cents(fills, Action.BUY) + broker.fee_cents(fills)


def _affordable(broker: Broker, leg: _BuyLeg, budget: int) -> int:
    """Most contracts whose cost fits in `budget`. Cost rises with count, so binary search."""
    depth = leg.book.depth()
    hi = math.floor(budget / 100 / leg.book.best + 1e-9)
    if not math.isinf(depth):
        hi = min(hi, math.floor(depth + 1e-9))
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cost = leg.cost(broker, mid)
        if cost is not None and cost <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _allocate(broker: Broker, legs: list[_BuyLeg], budget: int) -> tuple[list[PlannedOrder], list[SkippedLeg]]:
    """Spend at most `budget` cents across legs, each as close to its target as whole contracts allow.

    First each leg gets the most contracts that fit its target. Then, while cash is left, the
    leg furthest under its target (relative to it) gets one more, if that brings it closer.
    """
    spent = 0
    for leg in legs:
        leg.count = _affordable(broker, leg, min(leg.target, budget - spent))
        spent += leg.cost(broker, leg.count) or 0

    while True:
        best, best_score, best_extra = None, 0.0, 0
        for leg in legs:
            now = leg.cost(broker, leg.count) or 0
            more = leg.cost(broker, leg.count + 1)
            if more is None or spent + more - now > budget:
                continue
            short = leg.target - now
            # One more contract has to close more of the gap than it overshoots.
            if leg.target <= 0 or short * 2 < more - now:
                continue
            score = short / leg.target
            if score > best_score:
                best, best_score, best_extra = leg, score, more - now
        if best is None:
            break
        best.count += 1
        spent += best_extra

    orders, skipped = [], []
    for leg in legs:
        if leg.count:
            orders.append(_planned(leg.market, leg.side, leg.book, leg.book.take(leg.count), broker))
        else:
            reason = f"${leg.target / 100:,.2f} buys no contracts" if leg.target else "nothing left to spend"
            skipped.append(SkippedLeg(leg.market.id, leg.side, reason))
        depth = leg.book.depth()
        wanted = leg.target / 100 / leg.book.best
        if leg.count and not math.isinf(depth) and wanted > depth + 1:
            skipped.append(SkippedLeg(leg.market.id, leg.side, f"only {math.floor(depth)} contracts on offer"))
    return orders, skipped


def _split(
    legs: list[tuple[Market, Side]], quotes: list[Quote]
) -> tuple[list[tuple[Market, Side, Book]], list[SkippedLeg]]:
    """Legs that can trade, with their books, and the ones that can't, with why."""
    tradeable, skipped = [], []
    for (market, side), quote in zip(legs, quotes):
        if isinstance(quote, NoQuote):
            skipped.append(SkippedLeg(market.id, side, quote.reason))
        else:
            tradeable.append((market, side, quote))
    return tradeable, skipped


def plan_buy(account: Account, basket: Basket, amount: float, broker: Broker) -> Plan:
    budget = to_cents(amount, up=False)
    if budget > account.cash_cents:
        raise TradingError(f"Not enough cash: ${account.cash:,.2f} available")
    weights = {
        (pv.position.market_id, pv.position.side): pv.current_weight for pv in value_basket(basket).positions
    }
    legs = [(p.market, p.side) for p in basket.positions]
    tradeable, skipped = _split(legs, broker.books([(m, side, Action.BUY) for m, side in legs]))
    if not tradeable:
        raise TradingError(f"Nothing can be bought: {_reasons(skipped)}")

    buy_legs = [_BuyLeg(m, side, book, math.floor(budget * weights[(m.id, side)])) for m, side, book in tradeable]
    orders, more = _allocate(broker, buy_legs, budget)
    if not orders:
        raise TradingError("Amount is too small to buy a contract of any position")
    return Plan(BasketOrderKind.BUY, amount, orders, skipped + more)


def _sell_orders(
    broker: Broker, sells: list[tuple[Holding, int]]
) -> tuple[list[PlannedOrder], list[SkippedLeg]]:
    """Sell `count` of each holding into the bids. What the bids can't absorb stays held."""
    quotes = broker.books([(h.market, h.side, Action.SELL) for h, _ in sells])
    orders, skipped = [], []
    for (h, count), quote in zip(sells, quotes):
        if isinstance(quote, NoQuote):
            skipped.append(SkippedLeg(h.market_id, h.side, quote.reason))
            continue
        fills = quote.take(count)
        filled = round(sum(q for _, q in fills))
        if filled:
            orders.append(_planned(h.market, h.side, quote, fills, broker))
        if filled < count:
            skipped.append(SkippedLeg(h.market_id, h.side, f"{count - filled} contracts left: not enough bids"))
    return orders, skipped


def _reasons(skipped: list[SkippedLeg]) -> str:
    return "; ".join(f"{s.market_id} {s.side} {s.reason}" for s in skipped)


def plan_sell(account: Account, basket: Basket, fraction: float, broker: Broker) -> Plan:
    """Sell `fraction` of every holding, rounded down."""
    holdings = list(_holdings(account, basket).values())
    if not holdings:
        raise TradingError("No holdings in this basket")
    sells = [(h, h.contracts if fraction >= 1 else math.floor(h.contracts * fraction)) for h in holdings]
    orders, skipped = _sell_orders(broker, [(h, n) for h, n in sells if n > 0])
    if not orders:
        raise TradingError(f"Nothing to sell: {_reasons(skipped) or 'every holding rounds down to 0 contracts'}")
    return Plan(BasketOrderKind.SELL, fraction, orders, skipped)


def _index_needs_reset(basket: Basket) -> bool:
    valuation = value_basket(basket)
    return any(not p.market.is_open for p in basket.positions) or valuation.max_drift >= REBALANCE_TOLERANCE


def plan_rebalance(account: Account, basket: Basket, broker: Broker) -> Plan:
    held = _holdings(account, basket)
    if not held:
        raise TradingError("No holdings in this basket")
    try:
        rebalanced_weights(basket)
    except BasketError as e:
        raise TradingError(str(e)) from e

    targets = target_weights(basket, held)
    value = sum(_mark(h) for h in held.values())
    markets = {p.market_id: p.market for p in basket.positions} | {h.market_id: h.market for h in held.values()}

    sells, buys = [], []
    for (market_id, side), target in targets.items():
        market = markets[market_id]
        holding = held.get((market_id, side))
        if not market.is_open or value <= 0:
            continue
        current = _mark(holding) if holding else 0.0
        if abs(current - target * value) / value < REBALANCE_TOLERANCE:
            continue
        # Sized in contracts at the mark, so the holding lands on target however much the trade costs.
        mark = market.price_for(side)
        want = round(target * value / mark) if mark > 0 else 0
        have = holding.contracts if holding else 0
        if want < have:
            sells.append((holding, have - want))
        elif want > have:
            buys.append((market, side, want - have))

    orders, skipped = _sell_orders(broker, sells)
    proceeds = sum(o.cash_cents for o in orders)
    if buys:
        tradeable, no_quote = _split(
            [(m, side) for m, side, _ in buys], broker.books([(m, side, Action.BUY) for m, side, _ in buys])
        )
        counts = {(m.id, side): n for m, side, n in buys}
        legs = []
        for m, side, book in tradeable:
            leg = _BuyLeg(m, side, book, 0)
            depth = book.depth()
            count = counts[(m.id, side)] if math.isinf(depth) else min(counts[(m.id, side)], math.floor(depth))
            leg.target = leg.cost(broker, count) or 0
            legs.append(leg)
        # Funded by the sells first; account cash covers what spreads and fees take off them.
        bought, more = _allocate(broker, legs, proceeds + account.cash_cents)
        orders += bought
        skipped += no_quote + [s for s in more if "buys no contracts" not in s.reason]

    reset = _index_needs_reset(basket)
    if not orders and not reset:
        if skipped:
            raise TradingError(f"Nothing can be rebalanced: {_reasons(skipped)}")
        raise TradingError("Holdings are already at target weights")
    return Plan(BasketOrderKind.REBALANCE, None, orders, skipped, reset_index=True)


def needs_rebalance(account: Account, basket: Basket, threshold: float = DEFAULT_DRIFT_THRESHOLD) -> bool:
    holdings = value_basket_holdings(account, basket)
    return bool(holdings.holdings) and holdings.max_drift > threshold


def _issue_shares(stake: Stake, value_before: float, flow: float, nav: float, now: datetime) -> None:
    """Issue (or redeem, for a negative flow) shares for `flow` dollars at the stake's value per share."""
    if stake.shares <= 0 and value_before > 0:
        # Holdings without shares (e.g. from before shares existed) are adopted at the NAV.
        stake.shares, stake.opened_nav, stake.opened_at = value_before / nav, nav, now
    if stake.shares <= 0 or value_before <= 0:
        # Nothing held (or it's worth nothing): start over at the basket's NAV.
        stake.shares = flow / nav if flow > 0 and nav > 0 else 0.0
        stake.opened_nav, stake.opened_at = (nav, now) if stake.shares else (None, None)
        return
    stake.shares = max(stake.shares + flow * stake.shares / value_before, 0.0)


def _book(account: Account, basket: Basket, held: dict[LegKey, Holding], stake: Stake, order: Order) -> int:
    """Book a fill to cash and holdings. Returns the cents it moved into the stake (negative out)."""
    key = (order.market.id, order.side)
    if order.action == Action.BUY:
        holding = held.get(key)
        if holding is None:
            holding = held[key] = Holding(basket=basket, market=order.market, side=order.side, contracts=0, cost_cents=0)
            account.holdings.append(holding)
        paid = order.gross_cents + order.fee_cents
        holding.contracts += order.filled_count
        holding.cost_cents += paid
        account.cash_cents -= paid
        return paid

    holding = held[key]
    basis = round(holding.cost_cents * order.filled_count / holding.contracts)
    proceeds = order.gross_cents - order.fee_cents
    order.realized_pnl_cents = proceeds - basis
    stake.realized_pnl_cents += proceeds - basis
    holding.contracts -= order.filled_count
    holding.cost_cents -= basis
    account.cash_cents += proceeds
    if holding.contracts == 0:
        account.holdings.remove(holding)
        del held[key]
    return -proceeds


def snapshot(session: Session, account: Account, now: datetime, stakes: list[Stake] | None = None) -> None:
    """Record the account's equity, and the value of `stakes` (default all) that hold something."""
    session.add(AccountValue(account=account, ts=now, equity=value_account(account).equity))
    for stake in account.stakes if stakes is None else stakes:
        holdings = value_basket_holdings(account, stake.basket)
        if holdings.holdings or stake.shares > 0:
            session.add(
                StakeValue(
                    stake_id=stake.id, ts=now, value=holdings.value, share_value=holdings.share_value, nav=holdings.nav
                )
            )


def _settle_up(
    session: Session,
    account: Account,
    basket: Basket,
    record: BasketOrder,
    orders: list[Order],
    now: datetime,
    reset_index: bool = False,
) -> None:
    """Book filled orders, issue or redeem shares for the cash that moved, and snapshot."""
    stake = get_stake(account, basket)
    before = value_basket_holdings(account, basket).value
    nav = value_basket(basket).nav
    held = _holdings(account, basket)
    flow = 0
    for order in orders:
        record.orders.append(order)
        if order.filled_count:
            flow += _book(account, basket, held, stake, order)
    if reset_index:
        apply_rebalance(basket)
    if held:
        _issue_shares(stake, before, flow / 100, nav, now)
    else:
        stake.shares, stake.opened_nav, stake.opened_at = 0.0, None, None
    session.add(record)
    session.flush()  # gives a new stake its id for the snapshot
    snapshot(session, account, now, [stake])


def execute(
    session: Session,
    account: Account,
    basket: Basket,
    plan: Plan,
    broker: Broker,
    origin: OrderOrigin = OrderOrigin.MANUAL,
) -> BasketOrder:
    """Send the plan's orders in order, and book each fill to cash, holdings and shares."""
    record = BasketOrder(
        account_id=account.id,
        basket_id=basket.id,
        basket_name=basket.name,
        kind=plan.kind,
        origin=origin,
        amount=plan.amount,
    )
    orders = []
    for planned in plan.orders:
        request = planned.request
        fill = broker.execute(request)
        order = Order(
            market=request.market,
            side=request.side,
            action=request.action,
            count=request.count,
            limit_price=request.limit_price,
            filled_count=fill.count,
            fill_price=fill.price,
            gross_cents=fill.gross_cents,
            fee_cents=fill.fee_cents,
            realized_pnl_cents=None,
        )
        orders.append(order)
    _settle_up(session, account, basket, record, orders, utcnow(), reset_index=plan.reset_index)
    return record


def settle(session: Session, now: datetime) -> int:
    """Pay out holdings in resolved markets to cash, one payout order per account and basket. No fees."""
    held = session.scalars(select(Holding).join(Market).where(Market.settlement_price.is_not(None))).all()
    groups: dict[tuple[int, int], list[Holding]] = defaultdict(list)
    for h in held:
        groups[(h.account_id, h.basket_id)].append(h)

    for holdings in groups.values():
        account, basket = holdings[0].account, holdings[0].basket
        record = BasketOrder(
            account_id=account.id,
            basket_id=basket.id,
            basket_name=basket.name,
            kind=BasketOrderKind.SETTLE,
            origin=OrderOrigin.SETTLEMENT,
            amount=None,
        )
        orders = []
        for h in holdings:
            payout = h.market.price_for(h.side)
            order = Order(
                market=h.market,
                side=h.side,
                action=Action.SELL,
                count=h.contracts,
                limit_price=payout,
                filled_count=h.contracts,
                fill_price=payout,
                gross_cents=to_cents(h.contracts * payout, up=False),
                fee_cents=0,
                realized_pnl_cents=None,
            )
            orders.append(order)
        _settle_up(session, account, basket, record, orders, now)
    return len(held)
