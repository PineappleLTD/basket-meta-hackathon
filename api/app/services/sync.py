from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.kalshi import MarketSource
from app.models import Account, AccountValue, Basket, BasketNav, Market, MarketPrice, utcnow
from app.services.baskets import value_basket
from app.services.trading import value_account

# New markets get their history fetched and inserted this many at a time, so a first
# sync of tens of thousands of markets doesn't hold millions of rows in memory at once.
_BACKFILL_CHUNK = 1000


def sync_markets(session: Session, source: MarketSource, backfill_days: int) -> dict[str, int]:
    """Upsert the source's markets on external_id.

    At one shared timestamp, records a price point per market and a NAV point per
    basket and an equity point per trading account. Markets the source no longer returns are marked closed. Markets seen
    for the first time get `backfill_days` of daily history from the source.
    A ticker the source returns more than once is stored once, from its last copy.
    """
    now = utcnow()
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

    for basket in session.scalars(select(Basket)):
        session.add(BasketNav(basket=basket, ts=now, nav=value_basket(basket).nav))
    for account in session.scalars(select(Account)):
        session.add(AccountValue(account=account, ts=now, equity=value_account(account).equity))
    session.commit()
    return {"created": len(new), "updated": len(fetched) - len(new), "closed": len(closed)}
