import logging
from datetime import datetime

from sqlalchemy import insert, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.brokers import Broker
from app.kalshi import MarketSource
from app.models import Account, AccountMode, Basket, BasketNav, BasketPosition, Holding, Market, MarketPrice, utcnow
from app.services import automation
from app.services.baskets import value_basket
from app.services.trading import settle, snapshot

log = logging.getLogger(__name__)

# New markets get their history fetched and inserted this many at a time, so a first
# sync of tens of thousands of markets doesn't hold millions of rows in memory at once.
_BACKFILL_CHUNK = 1000


def sync_markets(session: Session, source: MarketSource, backfill_days: int, now: datetime | None = None) -> dict[str, int]:
    """Upsert the source's markets on external_id, then settle what resolved.

    At one shared timestamp, records a price point per market. Markets the source no longer
    returns are marked closed, and closed markets that a basket or holding uses are looked up
    until they resolve: then they take their payout as price, and holdings in them are paid
    out to cash. Markets seen for the first time get `backfill_days` of daily history from
    the source. A ticker the source returns more than once is stored once, from its last copy.
    Finally a NAV point per basket, and an equity point per account and stake, are recorded.
    """
    now = now or utcnow()
    fetched = list({d.external_id: d for d in source.fetch_markets()}.values())
    existing = {m.external_id: m for m in session.scalars(select(Market))}
    new = []
    for data in fetched:
        market = existing.get(data.external_id)
        if market is None:
            market = Market(external_id=data.external_id)
            session.add(market)
            new.append(market)
        for field, value in vars(data).items():
            setattr(market, field, value)
        market.quoted_at = now

    seen = {d.external_id for d in fetched}
    closed = [m for ticker, m in existing.items() if ticker not in seen and m.is_open]
    for market in closed:
        market.is_open = False
    session.flush()  # assigns ids to new markets

    by_ticker = {m.external_id: m for m in [*existing.values(), *new]}
    rows = [{"market_id": by_ticker[d.external_id].id, "ts": now, "yes_price": d.yes_price} for d in fetched]
    if rows:
        session.execute(insert(MarketPrice), rows)

    if backfill_days:
        new_tickers = [m.external_id for m in new]
        for i in range(0, len(new_tickers), _BACKFILL_CHUNK):
            history = source.fetch_history(new_tickers[i : i + _BACKFILL_CHUNK], backfill_days)
            # Keyed on (market, ts) so a repeated candle can't add a second row.
            points = {
                (by_ticker[ticker].id, ts): price
                for ticker, series in history.items()
                if ticker in by_ticker
                for ts, price in series
                if ts < now
            }
            if points:
                session.execute(
                    insert(MarketPrice),
                    [{"market_id": mid, "ts": ts, "yes_price": price} for (mid, ts), price in points.items()],
                )

    resolved = _resolve(session, source, now)
    session.commit()

    settled = 0
    try:
        settled = settle(session, now)
        session.commit()
    except StaleDataError:
        # A trade committed against the same account meanwhile; the next sync pays out instead.
        session.rollback()
        log.info("Account changed during settlement; retrying on the next sync")

    for basket in session.scalars(select(Basket)):
        session.add(BasketNav(basket=basket, ts=now, nav=value_basket(basket).nav))
    for account in session.scalars(select(Account)):
        snapshot(session, account, now)
    session.commit()
    return {
        "created": len(new),
        "updated": len(fetched) - len(new),
        "closed": len(closed),
        "resolved": resolved,
        "settled": settled,
    }


def _resolve(session: Session, source: MarketSource, now: datetime) -> int:
    """Look up results for closed, unresolved markets that a basket or holding uses."""
    used = or_(
        Market.id.in_(select(Holding.market_id)),
        Market.external_id.in_(select(BasketPosition.market_ticker)),
    )
    pending = session.scalars(
        select(Market).where(Market.is_open.is_(False), Market.settlement_price.is_(None), used)
    ).all()
    if not pending:
        return 0
    try:
        results = source.fetch_results([m.external_id for m in pending])
    except Exception:
        log.warning("Couldn't fetch market results; trying again on the next sync", exc_info=True)
        return 0
    resolved = [m for m in pending if m.external_id in results]
    for market in resolved:
        payout = results[market.external_id]
        market.settlement_price = market.yes_price = payout
        market.yes_bid = market.yes_ask = None
        market.settled_at = now
    if resolved:
        session.execute(
            insert(MarketPrice), [{"market_id": m.id, "ts": now, "yes_price": m.yes_price} for m in resolved]
        )
    return len(resolved)


def refresh(
    session: Session, source: MarketSource, brokers: dict[AccountMode, Broker], backfill_days: int
) -> dict[str, int]:
    """Sync markets, then place the automated trades that are due."""
    now = utcnow()
    counts = sync_markets(session, source, backfill_days, now)
    counts["automated"] = automation.run(session, brokers, now)
    return counts
