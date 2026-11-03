"""Backtest: one bet on a thesis vs the thesis's basket, on stored price history.

Theses come from two places: saved baskets (shared.db, with their saved weights), and
model-built baskets for each line of a theses file. `--generate` runs those through
the same selection /theses/select uses (no sync, nothing saved to the database) and
caches the picks, so later runs don't call the model again.

Money goes in at a formation date and is held to the last stored day:

- basket: buy and hold, like the app's index between rebalances. Model-built baskets
  are re-weighted by the optimizer using only prices up to formation;
- obvious bet: everything in the basket's most traded market, the one a person would
  most likely find and bet on alone;
- top pick: everything in the market the model rated most relevant (model-built only);
- single bets: each leg alone, to show how much one bet's result depends on luck.

Caveats: picks are chosen from today's open markets knowing today's news (the
price history before formation is all the optimizer sees, but not the selection),
closed markets keep their last price until a settlement is recorded, and entry cost
uses today's spread (history doesn't keep quotes).

Run from api/:
  .venv/bin/python backtest.py --generate     # call the model for backtest_theses.txt, then backtest
  .venv/bin/python backtest.py [--hold-days 45] [--json out.json]
"""

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.llm import LLMError
from app.main import make_llm
from app.models import Basket, Market, Side
from app.services import thesis as thesis_svc
from app.services.optimize import DEFAULT_MAX_WEIGHT, Candidate, optimize

FEE_RATE = 0.07
THESES_FILE = Path(__file__).parent / "backtest_theses.txt"
PICKS_FILE = Path(__file__).parent / "data" / "backtest_picks.json"


@dataclass
class Position:
    ticker: str
    side: Side
    # Saved baskets carry weights; model-built ones carry relevance and are weighted at formation.
    weight: float | None = None
    relevance: float | None = None


@dataclass
class Thesis:
    name: str
    source: str  # "saved" or "model"
    positions: list[Position]


@dataclass
class Outcome:
    total_return: float
    max_drawdown: float
    entry_cost: float

    @property
    def net_return(self) -> float:
        return (1 + self.total_return) * (1 - self.entry_cost) - 1


@dataclass
class Leg:
    ticker: str
    question: str
    side: str
    weight: float
    relevance: float | None
    volume: float
    outcome: Outcome


@dataclass
class ThesisResult:
    name: str
    source: str
    basket: Outcome
    obvious: Leg
    top_pick: Leg | None
    legs: list[Leg]
    skipped: int


def generate(session: Session, settings: Settings, theses: list[str], max_positions: int) -> None:
    """Run each thesis through the app's selection and cache the picks."""
    llm = make_llm(settings)
    if llm is None:
        raise SystemExit("No model API key configured (set META in .env)")
    cached = json.loads(PICKS_FILE.read_text()) if PICKS_FILE.exists() else {}
    for i, thesis in enumerate(theses, 1):
        if thesis in cached:
            continue
        print(f"[{i}/{len(theses)}] {thesis}", flush=True)
        try:
            selection = thesis_svc.select_markets(session, llm, thesis, max_positions)
        except (thesis_svc.ThesisError, LLMError) as e:
            print(f"  skipped: {e}")
            continue
        cached[thesis] = [
            {
                "ticker": w.candidate.market.external_id,
                "side": w.candidate.side.value,
                "relevance": w.candidate.relevance,
                "rationale": selection.rationales[w.candidate.market.id],
            }
            for w in selection.optimization.positions
        ]
        PICKS_FILE.write_text(json.dumps(cached, indent=1))  # after each, so a failure keeps the rest
        print(f"  {len(cached[thesis])} picks")


def load_theses(session: Session) -> list[Thesis]:
    theses = [
        Thesis(b.name, "saved", [Position(p.market_ticker, p.side, weight=p.target_weight) for p in b.positions])
        for b in session.scalars(select(Basket))
    ]
    if PICKS_FILE.exists():
        for name, picks in json.loads(PICKS_FILE.read_text()).items():
            theses.append(Thesis(name, "model", [
                Position(p["ticker"], Side(p["side"]), relevance=p["relevance"]) for p in picks
            ]))
    return theses


def load_prices(session: Session) -> dict[int, dict[date, float]]:
    """Each market's last YES price per UTC day."""
    daily: dict[int, dict[date, float]] = defaultdict(dict)
    for market_id, ts, price in session.execute(text("SELECT market_id, ts, yes_price FROM market_prices ORDER BY ts")):
        day = ts.date() if hasattr(ts, "date") else date.fromisoformat(ts[:10])
        daily[market_id][day] = price
    return daily


def daily_path(series: dict[date, float], market: Market, start: date, end: date) -> list[float] | None:
    """YES price each day from `start` to `end`, carrying the last price over gaps (and past
    close) and switching to the payout once settled. None if there's no price by `start`."""
    last = None
    for d in sorted(series):
        if d > start:
            break
        last = series[d]
    if last is None:
        return None
    path, day = [], start
    while day <= end:
        if market.settlement_price is not None and market.settled_at and market.settled_at.date() <= day:
            last = market.settlement_price
        else:
            last = series.get(day, last)
        path.append(last)
        day += timedelta(days=1)
    return path


def side_path(yes_path: list[float], side: Side) -> np.ndarray:
    p = np.array(yes_path)
    return p if side == Side.YES else 1 - p


def entry_cost(market: Market, side: Side, price: float) -> float:
    """Fees plus half the spread, as a share of the money put in. The spread is today's
    relative to today's price, since a market's price may have moved a lot since `price`."""
    half_spread = 0.0
    now = market.price_for(side)
    if market.yes_bid is not None and market.yes_ask is not None and now > 0:
        half_spread = max(market.yes_ask - market.yes_bid, 0) / 2 / now
    # Fee per contract is 0.07 p (1 - p), so per dollar it's 0.07 (1 - p).
    return min(FEE_RATE * (1 - price) + half_spread, 1.0)


def outcome(nav: np.ndarray, cost: float) -> Outcome:
    drawdown = float(np.max(1 - nav / np.maximum.accumulate(nav)))
    return Outcome(float(nav[-1] / nav[0] - 1), drawdown, cost)


def as_of(market: Market, yes_price: float) -> Market:
    """A detached copy of `market` priced as at formation, for the optimizer."""
    return Market(id=market.id, external_id=market.external_id, event_ticker=market.event_ticker,
                  question=market.question, yes_price=yes_price, yes_bid=market.yes_bid,
                  yes_ask=market.yes_ask, volume=market.volume, is_open=True)


def formation_weights(priced, prices, formation, max_weight) -> list[float]:
    """Optimizer weights from prices up to formation, in `priced` order (0 for dropped duplicates)."""
    order = sorted(priced, key=lambda x: x[0].relevance, reverse=True)  # decides which duplicate stays
    cands = [Candidate(as_of(m, sides[0] if pos.side == Side.YES else 1 - sides[0]), pos.side, pos.relevance)
             for pos, m, sides in order]
    lookback = {m.id: {d: p for d, p in prices.get(m.id, {}).items() if d <= formation} for _, m, _ in priced}
    result = optimize(cands, lookback, max_weight)
    by_id = {w.candidate.market.id: w.weight for w in result.positions}
    return [by_id.get(m.id, 0.0) for _, m, _ in priced]


def run_thesis(thesis: Thesis, markets_by_ticker, prices, formation, end, max_weight) -> ThesisResult | None:
    priced = []
    for pos in thesis.positions:
        market = markets_by_ticker.get(pos.ticker)
        path = daily_path(prices.get(market.id, {}), market, formation, end) if market else None
        if path is None:
            continue
        sides = side_path(path, pos.side)
        if sides[0] < 0.01:  # can't be bought at formation
            continue
        priced.append((pos, market, sides))
    if len(priced) < 2:
        return None

    if thesis.source == "model":
        weights = formation_weights(priced, prices, formation, max_weight)
    else:
        total = sum(pos.weight for pos, _, _ in priced)
        weights = [pos.weight / total for pos, _, _ in priced]

    legs = []
    nav = np.zeros(len(priced[0][2]))
    cost = 0.0
    for (pos, market, sides), w in zip(priced, weights):
        leg_cost = entry_cost(market, pos.side, sides[0])
        nav += w * 100 * sides / sides[0]  # units fixed at formation
        cost += w * leg_cost
        legs.append(Leg(market.external_id, market.question, pos.side.value, w, pos.relevance, market.volume,
                        outcome(sides, leg_cost)))
    rated = [leg for leg in legs if leg.relevance is not None]
    return ThesisResult(
        thesis.name,
        thesis.source,
        outcome(nav, cost),
        max(legs, key=lambda leg: leg.volume),
        max(rated, key=lambda leg: (leg.relevance, leg.volume)) if rated else None,
        legs,
        len(thesis.positions) - len(priced),
    )


def pct(x: float) -> str:
    return f"{x:+.1%}"


def report_thesis(r: ThesisResult) -> None:
    singles = [leg.outcome.net_return for leg in r.legs]
    beat = sum(s > r.basket.net_return for s in singles)
    print(f"\n[{r.source}] {r.name}  ({len(r.legs)} legs priced at formation, {r.skipped} not yet listed)")
    print(f"  basket       net {pct(r.basket.net_return):>7}  max drawdown {r.basket.max_drawdown:5.1%}")
    for label, leg in (("obvious bet", r.obvious), ("top pick", r.top_pick)):
        if leg:
            print(f"  {label:<12} net {pct(leg.outcome.net_return):>7}  max drawdown {leg.outcome.max_drawdown:5.1%}"
                  f"  {leg.side.upper()} {leg.question[:70]}")
    print(f"  single bets  best {pct(max(singles))}, median {pct(statistics.median(singles))},"
          f" worst {pct(min(singles))}; {beat}/{len(singles)} beat the basket")


def report_summary(results: list[ThesisResult]) -> None:
    basket = [r.basket.net_return for r in results]
    rows = [("basket", basket), ("obvious bet", [r.obvious.outcome.net_return for r in results])]
    top = [(r.basket.net_return, r.top_pick.outcome.net_return) for r in results if r.top_pick]
    if top:
        rows.append(("top pick", [t for _, t in top]))
    rows.append(("any one bet", [leg.outcome.net_return for r in results for leg in r.legs]))

    print(f"\n== across {len(results)} theses ==")
    print(f"{'':14} {'n':>4} {'mean':>7} {'median':>7} {'worst':>7} {'best':>8} {'sd':>7} {'lost money':>11}")
    for label, xs in rows:
        sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
        lost = sum(x < 0 for x in xs) / len(xs)
        print(f"{label:14} {len(xs):>4} {pct(statistics.mean(xs)):>7} {pct(statistics.median(xs)):>7}"
              f" {pct(min(xs)):>7} {pct(max(xs)):>8} {sd:>7.1%} {lost:>11.0%}")

    obvious = [r.obvious.outcome.net_return for r in results]
    print(f"\nbasket beat the obvious bet on {sum(b > o for b, o in zip(basket, obvious))}/{len(results)} theses")
    if top:
        print(f"basket beat the top pick on {sum(b > t for b, t in top)}/{len(top)} theses")
    big_loss = lambda xs: sum(x < -0.2 for x in xs)  # noqa: E731
    print(f"lost over 20%: basket {big_loss(basket)}/{len(basket)}, obvious bet {big_loss(obvious)}/{len(obvious)}")
    dd_b = statistics.median(r.basket.max_drawdown for r in results)
    dd_o = statistics.median(r.obvious.outcome.max_drawdown for r in results)
    print(f"median max drawdown: basket {dd_b:.1%}, obvious bet {dd_o:.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--generate", action="store_true", help=f"build baskets for {THESES_FILE.name} first")
    parser.add_argument("--hold-days", type=int, default=45, help="days held after formation")
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--max-weight", type=float, default=DEFAULT_MAX_WEIGHT)
    parser.add_argument("--json", help="also write every result to this file")
    args = parser.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url, settings.shared_database)
    with Session(engine) as session:
        if args.generate:
            lines = (line.strip() for line in THESES_FILE.read_text().splitlines())
            generate(session, settings, [t for t in lines if t and not t.startswith("#")], args.max_positions)
        markets_by_ticker = {m.external_id: m for m in session.scalars(select(Market))}
        prices = load_prices(session)
        end = max(max(series) for series in prices.values())
        formation = end - timedelta(days=args.hold_days)
        results = [r for t in load_theses(session)
                   if (r := run_thesis(t, markets_by_ticker, prices, formation, end, args.max_weight))]

    print(f"\nbought {formation}, held to {end} ({args.hold_days} days); returns are after entry fees and spread")
    for r in results:
        report_thesis(r)
    if results:
        report_summary(results)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"formation": str(formation), "end": str(end), "theses": [asdict(r) for r in results]},
                      f, indent=1)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
