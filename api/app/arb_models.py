"""Additive tables for the arbitrage scanner; existing tables stay intact."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.models import utcnow


class MarketTerms(Base):
    """A market's strike and event, written on every sync, for the arbitrage scanner."""

    __tablename__ = "arb_terms"

    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True)
    # "greater" (YES above floor_strike), "less" (below cap_strike), "between", "custom", ...
    strike_type: Mapped[str | None] = mapped_column(String(32))
    floor_strike: Mapped[float | None]
    cap_strike: Mapped[float | None]
    # The outcome a YES contract names, e.g. "Above 3.75%", and what the whole event asks.
    outcome: Mapped[str | None] = mapped_column(Text)
    event_title: Mapped[str | None] = mapped_column(Text)
    # At most one market in the event resolves YES.
    mutually_exclusive: Mapped[bool] = mapped_column(default=False)
    # Active markets in the event when last synced, counting ones the volume filter dropped.
    event_size: Mapped[int | None]
    # Hash of the rules with the market's own strike blanked out: equal ones measure the same thing
    # against different thresholds.
    strike_template: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ArbOpportunity(Base):
    """A group of markets whose prices broke a rule of probability, as last seen by a scan.

    One row per distinct set of legs, so the log shows how often each appeared and how long it lasted.
    """

    __tablename__ = "arb_opportunities"
    __table_args__ = (Index("ix_arb_opportunities_active_profit", "active", "profit_cents"),)

    # Hash of the kind and its legs.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # ladder | bracket_no | bracket_yes
    kind: Mapped[str] = mapped_column(String(16))
    # Always true now; kept so existing tables need no ALTER TABLE.
    guaranteed: Mapped[bool] = mapped_column(default=True)
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    # [{market_id, ticker, question, outcome, side, best_price, avg_price, closes_at}]
    legs: Mapped[list] = mapped_column(JSON)
    # What one set (one contract of every leg) pays at least, whatever happens.
    payout_per_set_cents: Mapped[int]
    # Per set at the best prices, after fees (fees unrounded). Dollars.
    edge_per_set: Mapped[float]
    # The most profitable size against the order books, and its money. None when depth is unknown.
    sets: Mapped[int | None]
    cost_cents: Mapped[int | None]
    payout_cents: Mapped[int | None]
    profit_cents: Mapped[int | None]
    # When the last leg closes: the money is tied up until then.
    closes_at: Mapped[datetime | None]
    # Priced against live order books, so `sets` is backed by real depth.
    confirmed: Mapped[bool] = mapped_column(default=False)
    # Found by the latest scan.
    active: Mapped[bool] = mapped_column(default=True)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
    times_seen: Mapped[int] = mapped_column(default=1)
    best_profit_cents: Mapped[int] = mapped_column(default=0)
    note: Mapped[str | None] = mapped_column(Text)


class ArbScan(Base):
    """One pass of the scanner, for the benchmark."""

    __tablename__ = "arb_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    duration_ms: Mapped[int] = mapped_column(default=0)
    # Open markets with terms, groups checked, groups close enough to price live, and opportunities found.
    markets: Mapped[int] = mapped_column(default=0)
    groups: Mapped[int] = mapped_column(default=0)
    candidates: Mapped[int] = mapped_column(default=0)
    found: Mapped[int] = mapped_column(default=0)
    profit_cents: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)


class LockBasket(Base):
    """Marks a basket as a locked arbitrage: one contract of every leg per set, held to settlement.

    Rebalances, buys by weight, news shifts and automations would trade the legs apart and break
    the floor, so they're refused for these baskets. The money fields add up every lock placed into it.
    """

    __tablename__ = "lock_baskets"

    basket_id: Mapped[int] = mapped_column(ForeignKey("shared.baskets.id", ondelete="CASCADE"), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    payout_per_set_cents: Mapped[int]
    sets: Mapped[int] = mapped_column(default=0)
    cost_cents: Mapped[int] = mapped_column(default=0)
    payout_cents: Mapped[int] = mapped_column(default=0)
    closes_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


def lock_for(session: Session | None, basket_id: int | None) -> LockBasket | None:
    """The lock a basket holds, or None for an ordinary (index) basket."""
    if session is None or basket_id is None:
        return None
    return session.get(LockBasket, basket_id)
