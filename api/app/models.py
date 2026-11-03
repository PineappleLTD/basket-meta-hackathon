from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """Timestamps are stored as UTC; treat naive input as UTC too."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _str_enum(enum: type[StrEnum]) -> Enum:
    """Stored as the enum's values in a plain string column."""
    return Enum(enum, native_enum=False, values_callable=lambda e: [m.value for m in e])


class Side(StrEnum):
    YES = "yes"
    NO = "no"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AccountMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class BasketOrderKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    REBALANCE = "rebalance"
    # A resolved market paid out the holding.
    SETTLE = "settle"


class OrderOrigin(StrEnum):
    """What placed a basket order: the user, one of their automations, or a market resolving."""

    MANUAL = "manual"
    RECURRING = "recurring"
    AUTO_REBALANCE = "auto_rebalance"
    CONDITIONAL = "conditional"
    SETTLEMENT = "settlement"
    NEWS = "news"


class TriggerCondition(StrEnum):
    """When a conditional order fires, against the basket's NAV."""

    AT_OR_BELOW = "at_or_below"
    AT_OR_ABOVE = "at_or_above"


class ConditionalStatus(StrEnum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    # Fired, but the order couldn't be placed (e.g. not enough cash).
    FAILED = "failed"


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Kalshi's market ticker.
    external_id: Mapped[str] = mapped_column(String(200), unique=True)
    # Markets in one event share it, e.g. every country in "2030 World Cup Champion".
    event_ticker: Mapped[str | None] = mapped_column(String(200), index=True)
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    # Probability-style price of the YES outcome, 0..1. NO price is 1 - yes_price.
    yes_price: Mapped[float]
    # Top of book. None when that side has no orders.
    yes_bid: Mapped[float | None]
    yes_ask: Mapped[float | None]
    volume: Mapped[float] = mapped_column(default=0.0)
    closes_at: Mapped[datetime | None]
    is_open: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    # When yes_bid/yes_ask were last read from the source.
    quoted_at: Mapped[datetime | None]
    # What a YES contract paid once the market resolved (1 or 0; scalar markets in between).
    # yes_price is set to it too, so marks and NAVs use the payout.
    settlement_price: Mapped[float | None]
    settled_at: Mapped[datetime | None]

    @property
    def is_settled(self) -> bool:
        return self.settlement_price is not None

    def price_for(self, side: Side) -> float:
        return self.yes_price if side == Side.YES else 1.0 - self.yes_price


class MarketPrice(Base):
    """One observed YES price. Every sync writes a row per market at a shared ts."""

    __tablename__ = "market_prices"
    # At most one price per market per timestamp.
    __table_args__ = (Index("ix_market_prices_market_ts", "market_id", "ts", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    yes_price: Mapped[float]


class User(Base):
    """Stored in shared.db. SQLite can't enforce foreign keys across database files, so the
    references to users.id from app.db tables are unchecked."""

    __tablename__ = "users"
    __table_args__ = {"schema": "shared"}

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lowercased, so sign-in is case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuthSession(Base):
    """A signed-in session. Only a hash of the bearer token is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("shared.users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]

    user: Mapped[User] = relationship(lazy="joined")


class Basket(Base):
    """Stored in shared.db, so references to baskets.id from app.db tables are unchecked."""

    __tablename__ = "baskets"
    __table_args__ = (UniqueConstraint("owner_id", "name"), {"schema": "shared"})

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("shared.users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rebalanced_at: Mapped[datetime | None]

    positions: Mapped[list["BasketPosition"]] = relationship(
        back_populates="basket", cascade="all, delete-orphan", lazy="selectin"
    )


class BasketPosition(Base):
    """Stored in shared.db. Names its market by ticker: market ids are local to each app.db."""

    __tablename__ = "basket_positions"
    __table_args__ = (UniqueConstraint("basket_id", "market_ticker", "side"), {"schema": "shared"})

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"))
    market_ticker: Mapped[str] = mapped_column(ForeignKey("markets.external_id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    target_weight: Mapped[float]
    # Contracts held per basket share. Set so the basket's NAV starts at BASE_NAV.
    units: Mapped[float]

    basket: Mapped[Basket] = relationship(back_populates="positions")
    market: Mapped[Market] = relationship(lazy="joined")

    @property
    def market_id(self) -> int:
        return self.market.id


class BasketNav(Base):
    """A basket's NAV at a point in time: at creation and on every sync."""

    __tablename__ = "basket_navs"
    __table_args__ = (Index("ix_basket_navs_basket_ts", "basket_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    nav: Mapped[float]

    basket: Mapped[Basket] = relationship()


class Account(Base):
    """A user's trading account. Each user has at most one per mode.

    Money that changes hands is kept in whole cents. `version` is bumped on every write, so
    two trades planned against the same balance can't both commit.
    """

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("shared.users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[AccountMode] = mapped_column(_str_enum(AccountMode))
    # Paper accounts start at Settings.paper_starting_cash.
    cash_cents: Mapped[int]
    starting_cash_cents: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    reset_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1)

    holdings: Mapped[list["Holding"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )
    stakes: Mapped[list["Stake"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )

    __mapper_args__ = {"version_id_col": version}

    @property
    def cash(self) -> float:
        return self.cash_cents / 100

    @property
    def starting_cash(self) -> float:
        return self.starting_cash_cents / 100


class Holding(Base):
    """Contracts an account holds on behalf of one of its owner's baskets."""

    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("account_id", "basket_id", "market_id", "side"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    # RESTRICT: a basket can't be deleted while money is in it.
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="RESTRICT"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    contracts: Mapped[int]
    # Total paid for the contracts still held, fees included.
    cost_cents: Mapped[int]

    account: Mapped[Account] = relationship(back_populates="holdings")
    basket: Mapped[Basket] = relationship(lazy="joined")
    market: Mapped[Market] = relationship(lazy="joined")

    @property
    def cost(self) -> float:
        return self.cost_cents / 100


class Stake(Base):
    """An account's investment in one basket: its shares, and the automations that trade it.

    Shares make the holding's return comparable to the basket's NAV. The first buy issues
    shares at the NAV; later money in or out (buys, sells, payouts) issues or redeems shares
    at the holding's own value per share, so trading costs and rounding show up as the
    difference between the two. Kept while automations are set, even with nothing held.
    """

    __tablename__ = "stakes"
    __table_args__ = (UniqueConstraint("account_id", "basket_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), index=True)
    shares: Mapped[float] = mapped_column(default=0.0)
    # Basket NAV when the current run of shares was first issued (the holding's starting share value).
    opened_nav: Mapped[float | None]
    opened_at: Mapped[datetime | None]
    # Realized on sells and payouts since the account was opened or reset.
    realized_pnl_cents: Mapped[int] = mapped_column(default=0)
    # Rebalance on a sync when any holding drifts further than this from target. None = off.
    auto_rebalance_drift: Mapped[float | None]
    # Buy this much every `recurring_days`, starting at next_recurring_at. None = off.
    recurring_amount_cents: Mapped[int | None]
    recurring_days: Mapped[int | None]
    next_recurring_at: Mapped[datetime | None]
    # What the last automated action did or why it failed, for the UI.
    automation_note: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account] = relationship(back_populates="stakes")
    basket: Mapped[Basket] = relationship(lazy="joined")

    @property
    def realized_pnl(self) -> float:
        return self.realized_pnl_cents / 100


class StakeValue(Base):
    """A stake's value and value per share after each trade and on every sync."""

    __tablename__ = "stake_values"
    __table_args__ = (Index("ix_stake_values_stake_ts", "stake_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stake_id: Mapped[int] = mapped_column(ForeignKey("stakes.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    value: Mapped[float]
    # None while nothing is held.
    share_value: Mapped[float | None]
    # The basket's NAV at ts, so the two can be compared without lining up timestamps.
    nav: Mapped[float]


class ConditionalOrder(Base):
    """A basket buy or sell that waits until the basket's NAV crosses a level, checked on every sync.

    Covers limit buys (buy at or below), stop-losses (sell at or below) and take-profits
    (sell at or above). Fires once, as a market order.
    """

    __tablename__ = "conditional_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), index=True)
    action: Mapped[Action] = mapped_column(_str_enum(Action))
    # Dollars for a buy, share of holdings for a sell.
    amount: Mapped[float]
    condition: Mapped[TriggerCondition] = mapped_column(_str_enum(TriggerCondition))
    nav: Mapped[float]
    status: Mapped[ConditionalStatus] = mapped_column(_str_enum(ConditionalStatus), default=ConditionalStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    triggered_at: Mapped[datetime | None]
    basket_order_id: Mapped[int | None] = mapped_column(ForeignKey("basket_orders.id", ondelete="SET NULL"))
    # Why a fired order failed.
    note: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account] = relationship()
    basket: Mapped[Basket] = relationship(lazy="joined")
    basket_order: Mapped["BasketOrder | None"] = relationship()


class BasketOrder(Base):
    """One instruction against a basket (buy $X, sell a fraction, rebalance) and the orders it placed."""

    __tablename__ = "basket_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    # Kept as history after the basket is deleted.
    basket_id: Mapped[int | None] = mapped_column(ForeignKey("shared.baskets.id", ondelete="SET NULL"), index=True)
    basket_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[BasketOrderKind] = mapped_column(_str_enum(BasketOrderKind))
    origin: Mapped[OrderOrigin] = mapped_column(_str_enum(OrderOrigin), default=OrderOrigin.MANUAL)
    # Dollars for a buy, share of holdings for a sell, None for a rebalance or payout.
    amount: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    orders: Mapped[list["Order"]] = relationship(
        back_populates="basket_order", cascade="all, delete-orphan", lazy="selectin", order_by="Order.id"
    )
    cash_movement: Mapped["NewsCashMovement | None"] = relationship(
        back_populates="basket_order", cascade="all, delete-orphan", uselist=False
    )

    @property
    def basket_cash_delta(self) -> float:
        return self.cash_movement.delta_cents / 100 if self.cash_movement else 0.0


class NewsCashMovement(Base):
    """Cash assigned to or withdrawn from an ETF by a recorded basket operation."""
    __tablename__ = "news_cash_movements"
    basket_order_id: Mapped[int] = mapped_column(ForeignKey("basket_orders.id", ondelete="CASCADE"), primary_key=True)
    delta_cents: Mapped[int]
    basket_order: Mapped[BasketOrder] = relationship(back_populates="cash_movement")


class Order(Base):
    """One leg sent to the broker: buy or sell `count` contracts of one market outcome."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_order_id: Mapped[int] = mapped_column(ForeignKey("basket_orders.id", ondelete="CASCADE"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    action: Mapped[Action] = mapped_column(_str_enum(Action))
    count: Mapped[int]
    # Worst price the order may fill at: the deepest book level it was sized against. A
    # payout's is the settlement price.
    limit_price: Mapped[float]
    filled_count: Mapped[int]
    # Average fill price per contract; None when nothing filled.
    fill_price: Mapped[float | None]
    # What changed hands before the fee: paid for a buy, received for a sell.
    gross_cents: Mapped[int] = mapped_column(default=0)
    fee_cents: Mapped[int] = mapped_column(default=0)
    # Sells and payouts only: proceeds less fee less the cost basis of the contracts sold.
    realized_pnl_cents: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    basket_order: Mapped[BasketOrder] = relationship(back_populates="orders")
    market: Mapped[Market] = relationship(lazy="joined")

    @property
    def gross(self) -> float:
        return self.gross_cents / 100

    @property
    def fee(self) -> float:
        return self.fee_cents / 100

    @property
    def realized_pnl(self) -> float | None:
        return None if self.realized_pnl_cents is None else self.realized_pnl_cents / 100


class AccountValue(Base):
    """An account's equity (cash + holdings at market) at creation, after each trade, and on every sync."""

    __tablename__ = "account_values"
    __table_args__ = (Index("ix_account_values_account_ts", "account_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    equity: Mapped[float]

    account: Mapped[Account] = relationship()
