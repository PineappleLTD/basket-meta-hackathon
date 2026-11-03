from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    AccountMode,
    Action,
    BasketOrderKind,
    ConditionalStatus,
    OrderOrigin,
    Side,
    TriggerCondition,
)


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or "." not in domain or " " in v or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Not a valid email address")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class AuthOut(BaseModel):
    # Send as `Authorization: Bearer <token>`.
    token: str
    user: UserOut


class MarketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    question: str
    category: str | None
    url: str | None
    event_ticker: str | None
    yes_price: float
    yes_bid: float | None
    yes_ask: float | None
    volume: float
    closes_at: datetime | None
    is_open: bool
    updated_at: datetime
    # What a YES contract paid, once resolved.
    settlement_price: float | None = None
    settled_at: datetime | None = None


class MarketPage(BaseModel):
    total: int
    items: list[MarketOut]


class PricePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    yes_price: float


class NavPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    nav: float


class SyncCounts(BaseModel):
    created: int
    updated: int
    closed: int
    # Closed markets whose result came in, and holdings paid out because of it.
    resolved: int
    settled: int
    # Basket orders placed by automations.
    automated: int
    # Arbitrage opportunities the scan after the sync found.
    opportunities: int = 0


class PositionIn(BaseModel):
    market_id: int
    side: Side = Side.YES
    weight: float | None = Field(default=None, gt=0, le=1)


class BasketIn(BaseModel):
    # Omit to have a model name the basket from its description and markets.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    positions: list[PositionIn] = Field(min_length=1, max_length=50)


class PositionOut(BaseModel):
    market: MarketOut
    side: Side
    target_weight: float
    current_weight: float
    drift: float
    units: float
    price: float
    value: float


class LockOut(BaseModel):
    """A basket that holds a locked arbitrage: one contract of every leg per set, held to settlement."""

    basket_id: int
    opportunity_id: str
    # ladder | time | bracket_no | bracket_yes
    kind: str
    title: str
    explanation: str
    # What each set pays at least, whatever happens.
    payout_per_set: float
    # Totals over every lock placed into this basket: sets bought, what they cost with fees, and their floor.
    sets: int
    cost: float
    payout: float
    profit: float
    closes_at: datetime | None
    created_at: datetime
    # For the signed-in account: "locked" while held, then "settled" or "closed". None outside an account.
    status: str | None = None
    held_sets: int | None = None


class BasketOut(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    rebalanced_at: datetime | None
    nav: float
    max_drift: float
    cash_weight: float = 0.0
    cash_target_weight: float = 0.0
    positions: list[PositionOut]
    # Set when the basket is a locked arbitrage rather than an index.
    lock: LockOut | None = None


class TradeOut(BaseModel):
    market_id: int
    side: Side
    action: str
    price: float
    current_units: float
    target_units: float
    delta_units: float


class RebalancePlan(BaseModel):
    nav: float
    max_drift: float
    threshold: float
    needs_rebalance: bool
    trades: list[TradeOut]


class RebalanceResult(BaseModel):
    trades: list[TradeOut]
    basket: BasketOut


class ThesisIn(BaseModel):
    thesis: str = Field(min_length=3, max_length=2000)
    max_positions: int = Field(default=10, ge=1, le=25)
    # Raised to 1 / positions when that's higher, so weights can still sum to 1.
    max_weight: float = Field(default=0.25, gt=0, le=1)


class ThesisPick(BaseModel):
    market: MarketOut
    side: Side
    # Share of the basket. Sums to 1 across picks.
    weight: float
    relevance: float
    rationale: str
    # Daily sd of the side's returns; estimated from peers when history is short.
    volatility: float
    volatility_estimated: bool
    # 0..1, from bid/ask spread and volume.
    liquidity: float
    # Share of basket variance. Sums to 1 across picks.
    risk_contribution: float


class DroppedPick(BaseModel):
    market: MarketOut
    side: Side
    # The kept pick whose daily returns this one tracks.
    duplicate_of: int
    correlation: float


class ThesisSelection(BaseModel):
    thesis: str
    summary: str
    sub_themes: list[str]
    keywords: list[str]
    candidates_considered: int
    picks: list[ThesisPick]
    dropped: list[DroppedPick]
    # Daily sd of the basket's returns.
    volatility: float
    # Weighted average of position volatilities over basket volatility; 1 = undiversified.
    diversification_ratio: float
    # Ready to send as `positions` to POST /baskets.
    positions: list[PositionIn]


class BuyIn(BaseModel):
    # Dollars to put in, split across positions by target weight. Fees come out of it.
    amount: float = Field(gt=0)
    # Plan and price the orders without placing them.
    dry_run: bool = False


class SellIn(BaseModel):
    # Share of each holding to sell, rounded down to whole contracts; 1 sells everything.
    fraction: float = Field(default=1.0, gt=0, le=1)
    dry_run: bool = False


class RebalanceIn(BaseModel):
    dry_run: bool = False


class HoldingOut(BaseModel):
    market: MarketOut
    side: Side
    contracts: int
    # Cost per contract, fees included.
    avg_cost: float
    cost: float
    # Mark: bid/ask mid, else last trade.
    price: float
    value: float
    # Selling now at the best bid, after fees. A closed market counts at its mark until it pays out.
    liquidation_value: float
    unrealized_pnl: float
    # Share of the basket's holdings value, and the share a rebalance would bring it to.
    weight: float
    target_weight: float
    drift: float


class BasketHoldingsOut(BaseModel):
    basket_id: int
    basket_name: str
    # The basket's index value, to compare share_value with.
    nav: float
    value: float
    liquidation_value: float
    cost: float
    unrealized_pnl: float
    # On sells and payouts in this basket since the account was opened or reset.
    realized_pnl: float
    max_drift: float
    # Shares are issued at the NAV on the first buy, then at share_value.
    shares: float
    share_value: float | None
    opened_nav: float | None
    opened_at: datetime | None
    # Since opened_at: the holding's return (share_value / opened_nav - 1) and the index's.
    holding_return: float | None
    index_return: float | None
    holdings: list[HoldingOut]
    cash: float = 0.0


class AccountOut(BaseModel):
    mode: AccountMode
    cash: float
    available_cash: float = 0.0
    starting_cash: float
    holdings_value: float
    # Cash plus holdings at market.
    equity: float
    # Cash plus holdings sold now at the best bids.
    liquidation_equity: float
    # Equity less starting cash.
    pnl: float
    realized_pnl: float
    created_at: datetime
    reset_at: datetime | None
    baskets: list[BasketHoldingsOut]


class EquityPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    equity: float


class StakePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    value: float
    share_value: float | None
    nav: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    side: Side
    action: Action
    count: int
    limit_price: float
    filled_count: int
    fill_price: float | None
    # Paid (buy) or received (sell) before the fee.
    gross: float
    fee: float
    realized_pnl: float | None
    created_at: datetime


class BasketOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    basket_id: int | None
    basket_name: str
    kind: BasketOrderKind
    origin: OrderOrigin
    amount: float | None
    created_at: datetime
    orders: list[OrderOut]
    basket_cash_delta: float = 0.0


class PlannedOrderOut(BaseModel):
    market_id: int
    side: Side
    action: Action
    count: int
    # Average price per contract, walking the book from best_price down to limit_price.
    price: float
    best_price: float
    limit_price: float
    # What walking the book costs over filling everything at best_price.
    price_impact: float
    fee: float
    # Negative for a buy.
    cash_delta: float


class SkippedLegOut(BaseModel):
    market_id: int
    side: Side
    reason: str


class TradePreview(BaseModel):
    kind: BasketOrderKind
    orders: list[PlannedOrderOut]
    skipped: list[SkippedLegOut]
    fees: float
    price_impact: float
    cash_delta: float
    cash_after: float
    basket_cash_delta: float = 0.0


class TradeResult(BaseModel):
    basket_order: BasketOrderOut
    skipped: list[SkippedLegOut]
    account: AccountOut


class AutomationIn(BaseModel):
    # Rebalance on a sync once any holding drifts this far from target. None turns it off.
    auto_rebalance_drift: float | None = Field(default=None, gt=0, le=1)
    # Buy `recurring_amount` dollars every `recurring_days`. Both or neither.
    recurring_amount: float | None = Field(default=None, gt=0)
    recurring_days: int | None = Field(default=None, ge=1, le=365)
    # First recurring buy. Defaults to the next sync.
    recurring_start: datetime | None = None

    @model_validator(mode="after")
    def _recurring_pair(self):
        if (self.recurring_amount is None) != (self.recurring_days is None):
            raise ValueError("Give both recurring_amount and recurring_days, or neither")
        return self


class AutomationOut(BaseModel):
    auto_rebalance_drift: float | None
    recurring_amount: float | None
    recurring_days: int | None
    next_recurring_at: datetime | None
    # What the last automated action did, or why it didn't.
    note: str | None


class ConditionalOrderIn(BaseModel):
    action: Action
    # Dollars for a buy; share of holdings (0..1] for a sell.
    amount: float = Field(gt=0)
    condition: TriggerCondition
    # Fires when the basket's NAV is at or past this.
    nav: float = Field(gt=0)

    @model_validator(mode="after")
    def _sell_fraction(self):
        if self.action == Action.SELL and self.amount > 1:
            raise ValueError("A sell's amount is the share of holdings to sell, at most 1")
        return self


class ConditionalOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    basket_id: int
    action: Action
    amount: float
    condition: TriggerCondition
    nav: float
    status: ConditionalStatus
    created_at: datetime
    triggered_at: datetime | None
    basket_order_id: int | None
    note: str | None


class ArbLegOut(BaseModel):
    market_id: int
    ticker: str
    question: str
    # The outcome the market names, when its question is shared with its siblings (e.g. "Above 3.75%").
    outcome: str | None = None
    url: str | None = None
    side: Side
    # Best ask when last priced, and the average over `sets` contracts walking the book.
    best_price: float
    avg_price: float | None = None
    closes_at: datetime | None = None


class OpportunityOut(BaseModel):
    id: str
    # ladder | time | bracket_no | bracket_yes | implication
    kind: str
    # False for implications: they rest on a model's reading of two questions.
    guaranteed: bool
    title: str
    explanation: str
    legs: list[ArbLegOut]
    # One set is one contract of every leg; it pays at least this whatever happens.
    payout_per_set: float
    # Per set at the best prices, after fees.
    edge_per_set: float
    # The most profitable size against the live order books. None when depth is unknown.
    sets: int | None
    cost: float | None
    payout: float | None
    profit: float | None
    # profit / cost, and that as a simple yearly rate until the last leg closes.
    return_pct: float | None
    annualized: float | None
    closes_at: datetime | None
    # Priced against live order books, so `sets` is backed by real depth.
    confirmed: bool
    active: bool
    first_seen: datetime
    last_seen: datetime
    times_seen: int
    best_profit: float
    note: str | None


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    started_at: datetime
    duration_ms: int
    # Open markets with terms, groups checked, groups priced live, and opportunities found.
    markets: int
    groups: int
    candidates: int
    found: int
    error: str | None


class ArbSummaryOut(BaseModel):
    scans: int
    last_scan: ScanOut | None
    # Distinct opportunities ever logged, how many are open now, and the profit open now (live-priced only).
    opportunities_seen: int
    active: int
    profit_available: float
    # Sum of each logged opportunity's best profit: what locking every one at its peak would have made.
    best_profit_seen: float
    # This user's locks: how many, what they cost, and the payout they're guaranteed.
    locks: int
    locked_cost: float
    locked_payout: float
    locked_profit: float


class ArbitrageOut(BaseModel):
    summary: ArbSummaryOut
    # Guaranteed kinds, most profit first.
    opportunities: list[OpportunityOut]
    # Model-proposed pairs priced the wrong way, checked in the last day. Not guaranteed.
    implications: list[OpportunityOut]


class ScanIn(BaseModel):
    # Sync markets from Kalshi first (about half a minute), so the scan screens fresh quotes.
    sync: bool = False


class ImplicationIn(BaseModel):
    query: str = Field(min_length=3, max_length=300)


class ImplicationOut(BaseModel):
    if_market: MarketOut
    then_market: MarketOut
    # "definitional": if_market is a special case of then_market. "causal": very likely, not by definition.
    relation: str
    confidence: float
    reason: str
    # YES on then_market plus NO on if_market at the stored quotes, after fees, per set. Positive when
    # the pair is priced as if the implication ran the other way.
    edge_per_set: float | None
    mispriced: bool
    opportunity_id: str | None


class ImplicationsOut(BaseModel):
    query: str
    candidates_considered: int
    pairs: list[ImplicationOut]


class LockIn(BaseModel):
    # Contracts of every leg. Omit for the most profitable size the order books allow.
    sets: int | None = Field(default=None, ge=1, le=100_000)
    dry_run: bool = False


class LockPreviewOut(BaseModel):
    opportunity_id: str
    sets: int
    orders: list[PlannedOrderOut]
    cost: float
    payout: float
    profit: float
    fees: float
    return_pct: float
    annualized: float | None
    cash_after: float


class LockResultOut(BaseModel):
    lock: LockOut
    basket_order: BasketOrderOut
    # Sets every leg filled; extra contracts on any leg were sold back.
    filled_sets: int
    note: str | None
    account: AccountOut
