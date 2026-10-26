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
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lowercased, so sign-in is case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuthSession(Base):
    """A signed-in session. Only a hash of the bearer token is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]

    user: Mapped[User] = relationship(lazy="joined")


class Basket(Base):
    __tablename__ = "baskets"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rebalanced_at: Mapped[datetime | None]

    positions: Mapped[list["BasketPosition"]] = relationship(
        back_populates="basket", cascade="all, delete-orphan", lazy="selectin"
    )


class BasketPosition(Base):
    __tablename__ = "basket_positions"
    __table_args__ = (UniqueConstraint("basket_id", "market_id", "side"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("baskets.id", ondelete="CASCADE"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    target_weight: Mapped[float]
    # Contracts held per basket share. Set so the basket's NAV starts at BASE_NAV.
    units: Mapped[float]

    basket: Mapped[Basket] = relationship(back_populates="positions")
    market: Mapped[Market] = relationship(lazy="joined")


class BasketNav(Base):
    """A basket's NAV at a point in time: at creation and on every sync."""

    __tablename__ = "basket_navs"
    __table_args__ = (Index("ix_basket_navs_basket_ts", "basket_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("baskets.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    nav: Mapped[float]

    basket: Mapped[Basket] = relationship()


class Account(Base):
    """A user's trading account. Each user has at most one per mode."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[AccountMode] = mapped_column(_str_enum(AccountMode))
    # Dollars. Paper accounts start at Settings.paper_starting_cash.
    cash: Mapped[float]
    starting_cash: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    reset_at: Mapped[datetime | None]

    holdings: Mapped[list["Holding"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )


class Holding(Base):
    """Contracts an account holds on behalf of one of its owner's baskets."""

    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("account_id", "basket_id", "market_id", "side"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    # RESTRICT: a basket can't be deleted while money is in it.
    basket_id: Mapped[int] = mapped_column(ForeignKey("baskets.id", ondelete="RESTRICT"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    contracts: Mapped[int]
    # Total paid for the contracts still held, fees included.
    cost: Mapped[float]

    account: Mapped[Account] = relationship(back_populates="holdings")
    basket: Mapped[Basket] = relationship(lazy="joined")
    market: Mapped[Market] = relationship(lazy="joined")


class BasketOrder(Base):
    """One instruction against a basket (buy $X, sell a fraction, rebalance) and the orders it placed."""

    __tablename__ = "basket_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    # Kept as history after the basket is deleted.
    basket_id: Mapped[int | None] = mapped_column(ForeignKey("baskets.id", ondelete="SET NULL"), index=True)
    basket_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[BasketOrderKind] = mapped_column(_str_enum(BasketOrderKind))
    # Dollars for a buy, share of holdings for a sell, None for a rebalance.
    amount: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    orders: Mapped[list["Order"]] = relationship(
        back_populates="basket_order", cascade="all, delete-orphan", lazy="selectin", order_by="Order.id"
    )


class Order(Base):
    """One leg sent to the broker: buy or sell `count` contracts of one market outcome."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_order_id: Mapped[int] = mapped_column(ForeignKey("basket_orders.id", ondelete="CASCADE"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    side: Mapped[Side] = mapped_column(_str_enum(Side))
    action: Mapped[Action] = mapped_column(_str_enum(Action))
    count: Mapped[int]
    # Quoted price the order was sized against; the limit for a live order.
    limit_price: Mapped[float]
    filled_count: Mapped[int]
    # Average fill price per contract; None when nothing filled.
    fill_price: Mapped[float | None]
    fee: Mapped[float]
    # Sells only: proceeds less fee less the cost basis of the contracts sold.
    realized_pnl: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    basket_order: Mapped[BasketOrder] = relationship(back_populates="orders")
    market: Mapped[Market] = relationship(lazy="joined")


class AccountValue(Base):
    """An account's equity (cash + holdings at market) at creation, after each trade, and on every sync."""

    __tablename__ = "account_values"
    __table_args__ = (Index("ix_account_values_account_ts", "account_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    equity: Mapped[float]

    account: Mapped[Account] = relationship()
