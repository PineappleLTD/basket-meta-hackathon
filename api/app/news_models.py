"""Additive tables for paper news rebalancing; existing account tables stay intact."""
from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class NewsContract(Base):
    __tablename__ = "news_contracts"
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True)
    rules: Mapped[str] = mapped_column(Text)
    deadline: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class NewsPolicy(Base):
    __tablename__ = "news_policies"
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="suggest")
    sensitivity: Mapped[float] = mapped_column(default=0.5)
    max_shift: Mapped[float] = mapped_column(default=0.05)


class NewsSignal(Base):
    __tablename__ = "news_signals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(String(3))
    published_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Only server-verified cached evidence enters this table. No client scores.
    payload: Mapped[dict] = mapped_column(JSON)


class NewsCash(Base):
    """Part of account cash reserved for this basket, including unallocated sale proceeds."""
    __tablename__ = "news_cash"
    __table_args__ = (UniqueConstraint("account_id", "basket_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="RESTRICT"))
    cents: Mapped[int] = mapped_column(default=0)


class NewsAllocation(Base):
    """Cash leg of the saved ETF target; market targets remain BasketPosition rows."""
    __tablename__ = "news_allocations"
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), primary_key=True)
    cash_weight: Mapped[float] = mapped_column(default=0.0)
    cash_units: Mapped[float] = mapped_column(default=0.0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class NewsCycle(Base):
    __tablename__ = "news_cycles"
    __table_args__ = (UniqueConstraint("account_id", "basket_id", "event_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="RESTRICT"), index=True)
    event_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="SELLING")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    note: Mapped[str | None] = mapped_column(Text)

    # Immutable goals plus cumulative confirmed fills. Assigned as a new dict on updates.
    payload: Mapped[dict] = mapped_column(JSON)


class NewsConsumption(Base):
    __tablename__ = "news_consumptions"
    __table_args__ = (UniqueConstraint("account_id", "basket_id", "signal_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("news_signals.id", ondelete="CASCADE"))


class ExchangeOrder(Base):
    """Committed submission intent. SUBMITTING/UNKNOWN are never resubmitted."""
    __tablename__ = "exchange_orders"
    __table_args__ = (UniqueConstraint("cycle_id", "phase", "leg_index", "attempt"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("news_cycles.id", ondelete="RESTRICT"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[str] = mapped_column(String(200))
    phase: Mapped[str] = mapped_column(String(8))
    leg_index: Mapped[int]
    attempt: Mapped[int]
    request: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="PREPARED")
    venue_order_id: Mapped[str | None] = mapped_column(String(200))
    venue_status: Mapped[str | None] = mapped_column(String(24))
    booked_count: Mapped[int] = mapped_column(default=0)
    cancel_attempted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    note: Mapped[str | None] = mapped_column(Text)
    last_evidence: Mapped[dict | None] = mapped_column(JSON)


class ExchangeFill(Base):
    __tablename__ = "exchange_fills"
    __table_args__ = (UniqueConstraint("scope", "venue_fill_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("exchange_orders.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[str] = mapped_column(String(200))
    venue_fill_id: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    count: Mapped[int]
    gross_cents: Mapped[int]
    fee_cents: Mapped[int]


class LiveControl(Base):
    __tablename__ = "live_controls"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="RESTRICT"))
    armed: Mapped[bool] = mapped_column(default=False)
    scope: Mapped[str] = mapped_column(String(200))
    connected_at: Mapped[datetime] = mapped_column(default=utcnow)
