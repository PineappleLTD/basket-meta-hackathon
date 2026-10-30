from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AccountMode, Action, BasketOrderKind, Side


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


class BasketOut(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    rebalanced_at: datetime | None
    nav: float
    max_drift: float
    positions: list[PositionOut]


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
    price: float
    value: float
    unrealized_pnl: float
    # Share of the basket's holdings value, and the share the basket targets.
    weight: float
    target_weight: float
    drift: float


class BasketHoldingsOut(BaseModel):
    basket_id: int
    basket_name: str
    value: float
    cost: float
    unrealized_pnl: float
    max_drift: float
    holdings: list[HoldingOut]


class AccountOut(BaseModel):
    mode: AccountMode
    cash: float
    starting_cash: float
    holdings_value: float
    # Cash plus holdings at market.
    equity: float
    # Equity less starting cash.
    pnl: float
    created_at: datetime
    reset_at: datetime | None
    baskets: list[BasketHoldingsOut]


class EquityPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    equity: float


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
    fee: float
    realized_pnl: float | None
    created_at: datetime


class BasketOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    basket_id: int | None
    basket_name: str
    kind: BasketOrderKind
    amount: float | None
    created_at: datetime
    orders: list[OrderOut]


class PlannedOrderOut(BaseModel):
    market_id: int
    side: Side
    action: Action
    count: int
    price: float
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
    cash_delta: float
    cash_after: float


class TradeResult(BaseModel):
    basket_order: BasketOrderOut
    skipped: list[SkippedLegOut]
    account: AccountOut
