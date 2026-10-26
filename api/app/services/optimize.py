"""Portfolio construction: turn chosen positions into weights.

Each position gets a risk budget, relevance x liquidity, and weights are set so
that each position's share of basket variance matches its share of the budget
(risk budgeting, a generalized risk parity). So:

- A volatile position gets less weight than a calm one with the same budget. A 3c
  long shot that swings 50% a day carries far more risk per dollar than an 80c
  favorite, so it ends up small.
- Correlated positions share risk, so together they get less than two independent
  ones would.
- Positions that are hard to trade (wide spread, thin volume) get smaller budgets.

Returns are daily changes in the held side's price, from stored price history.
Pairs whose returns are almost identical are the same bet twice; the less relevant
one is dropped before weighting.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market, MarketPrice, Side, utcnow

HISTORY_DAYS = 90
# Below this many daily returns, a market's own volatility estimate is too noisy to use.
MIN_OBS = 10
# Pairs need this many shared days before their correlation is trusted at all...
MIN_PAIR_OBS = 10
# ...and correlations are shrunk toward 0 by PRIOR_OBS / (PRIOR_OBS + shared days).
PRIOR_OBS = 20
# Positions this correlated (on at least DUPLICATE_MIN_OBS shared days) are the same bet.
DUPLICATE_CORRELATION = 0.85
DUPLICATE_MIN_OBS = 20
# Side prices are floored here before computing returns, so a 0.001 tick can't
# produce a 1000% return.
PRICE_FLOOR = 0.01
# A round trip costing this share of the price halves the liquidity score.
HALF_LIQUIDITY_SPREAD = 0.10
# Volume (contracts) at which the volume part of the liquidity score maxes out.
FULL_LIQUIDITY_VOLUME = 1_000_000
MIN_VOLUME_SCORE = 0.2
# Relative spread assumed when one side of the book is empty.
MISSING_QUOTE_SPREAD = 0.5
DEFAULT_MAX_WEIGHT = 0.25


@dataclass(frozen=True)
class Candidate:
    market: Market
    side: Side
    relevance: float


@dataclass(frozen=True)
class Weighted:
    candidate: Candidate
    weight: float
    # Daily standard deviation of the side's returns. Estimated from peers when the
    # market lacks history (see `volatility_estimated`).
    volatility: float
    volatility_estimated: bool
    liquidity: float
    # Share of the basket's variance this position contributes. Sums to 1.
    risk_contribution: float


@dataclass(frozen=True)
class Dropped:
    candidate: Candidate
    duplicate_of: Candidate
    correlation: float


@dataclass(frozen=True)
class Optimization:
    positions: list[Weighted]
    dropped: list[Dropped] = field(default_factory=list)
    # Daily volatility of the basket, from the same covariance used to weight it.
    volatility: float = 0.0
    # Weighted average volatility over basket volatility. 1 means no diversification.
    diversification_ratio: float = 1.0


def load_daily_prices(session: Session, market_ids: list[int], days: int = HISTORY_DAYS) -> dict[int, dict[date, float]]:
    """Each market's last recorded YES price per UTC day, over the last `days` days."""
    since = utcnow() - timedelta(days=days)
    rows = session.execute(
        select(MarketPrice.market_id, MarketPrice.ts, MarketPrice.yes_price)
        .where(MarketPrice.market_id.in_(market_ids), MarketPrice.ts >= since)
        .order_by(MarketPrice.ts)
    )
    daily: dict[int, dict[date, float]] = defaultdict(dict)
    for market_id, ts, price in rows:
        daily[market_id][ts.date()] = price  # ordered by ts, so the day's last price wins
    return daily


def side_returns(yes_prices: dict[date, float], side: Side) -> dict[date, float]:
    """Return on the held side for each day whose previous day also has a price."""
    returns = {}
    for day, yes in yes_prices.items():
        prev = yes_prices.get(day - timedelta(days=1))
        if prev is None:
            continue
        p0, p1 = (prev, yes) if side == Side.YES else (1 - prev, 1 - yes)
        returns[day] = max(p1, PRICE_FLOOR) / max(p0, PRICE_FLOOR) - 1
    return returns


def liquidity_score(market: Market, side: Side) -> float:
    """0..1: how cheaply the position can be traded, from spread and volume."""
    price = max(market.price_for(side), PRICE_FLOOR)
    if market.yes_bid is not None and market.yes_ask is not None and market.yes_ask >= market.yes_bid:
        relative_spread = (market.yes_ask - market.yes_bid) / price  # same width on both sides
    else:
        relative_spread = MISSING_QUOTE_SPREAD
    spread_score = 1 / (1 + relative_spread / HALF_LIQUIDITY_SPREAD)
    volume_score = math.log10(1 + market.volume) / math.log10(1 + FULL_LIQUIDITY_VOLUME)
    return spread_score * min(max(volume_score, MIN_VOLUME_SCORE), 1.0)


def _pair_stats(a: dict[date, float], b: dict[date, float]) -> tuple[float, int]:
    days = a.keys() & b.keys()
    if len(days) < 3:
        return 0.0, len(days)
    x = np.array([a[d] for d in days])
    y = np.array([b[d] for d in days])
    if x.std() == 0 or y.std() == 0:
        return 0.0, len(days)
    return float(np.corrcoef(x, y)[0, 1]), len(days)


def _volatilities(cands: list[Candidate], returns: list[dict[date, float]]) -> tuple[np.ndarray, list[bool]]:
    """Empirical volatility where there's enough history, else an estimate.

    A binary contract bought at p has return sd sqrt((1-p)/p) over its life, so
    cheaper contracts are riskier per dollar. Missing volatilities use that shape,
    scaled by how measured volatilities compare to it.
    """
    structural = np.array([math.sqrt((1 - p) / p) for p in
                           (min(max(c.market.price_for(c.side), PRICE_FLOOR), 1 - PRICE_FLOOR) for c in cands)])
    measured = [float(np.std(list(r.values()), ddof=1)) if len(r) >= MIN_OBS else None for r in returns]
    ratios = [m / s for m, s in zip(measured, structural) if m]
    scale = float(np.median(ratios)) if ratios else 1.0
    vols = np.array([m if m else scale * s for m, s in zip(measured, structural)])
    return vols, [not m for m in measured]


def _correlations(returns: list[dict[date, float]]) -> np.ndarray:
    """Shrunk pairwise correlations, projected to the nearest valid (PSD) matrix."""
    n = len(returns)
    corr = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho, obs = _pair_stats(returns[i], returns[j])
            if obs >= MIN_PAIR_OBS:
                corr[i, j] = corr[j, i] = rho * obs / (obs + PRIOR_OBS)
    values, vectors = np.linalg.eigh(corr)
    corr = vectors @ np.diag(np.clip(values, 1e-6, None)) @ vectors.T
    d = np.sqrt(np.diag(corr))
    return corr / np.outer(d, d)


def risk_budget_weights(cov: np.ndarray, budgets: np.ndarray, iterations: int = 500, tol: float = 1e-12) -> np.ndarray:
    """Long-only weights whose risk contributions w_i (Cov w)_i are proportional to `budgets`.

    Cyclical coordinate descent on 0.5 w'Cov w - sum(b log w), which has a unique
    minimizer whose normalized form is the risk-budget portfolio.
    """
    b = budgets / budgets.sum()
    w = b / np.sqrt(np.diag(cov))
    for _ in range(iterations):
        prev = w.copy()
        for i in range(len(w)):
            c = cov[i] @ w - cov[i, i] * w[i]
            w[i] = (-c + math.sqrt(c * c + 4 * cov[i, i] * b[i])) / (2 * cov[i, i])
        if np.max(np.abs(w - prev)) < tol:
            break
    return w / w.sum()


def cap_weights(weights: np.ndarray, max_weight: float) -> np.ndarray:
    """Clip weights at `max_weight`, handing the excess to uncapped positions pro rata."""
    w = weights / weights.sum()
    max_weight = max(max_weight, 1 / len(w))
    capped = np.zeros(len(w), dtype=bool)
    while (over := (w > max_weight + 1e-12) & ~capped).any():
        capped |= over
        free = w[~capped].sum()
        w[capped] = max_weight
        if free > 0:
            w[~capped] *= (1 - max_weight * capped.sum()) / free
    return w


def optimize(
    cands: list[Candidate],
    daily_prices: dict[int, dict[date, float]],
    max_weight: float = DEFAULT_MAX_WEIGHT,
) -> Optimization:
    """`cands` should be ordered most relevant first: that order decides which of a duplicate pair stays."""
    if not cands:
        return Optimization(positions=[])
    returns = [side_returns(daily_prices.get(c.market.id, {}), c.side) for c in cands]

    keep: list[int] = []
    dropped: list[Dropped] = []
    for i, cand in enumerate(cands):
        for k in keep:
            rho, obs = _pair_stats(returns[i], returns[k])
            if obs >= DUPLICATE_MIN_OBS and rho >= DUPLICATE_CORRELATION:
                dropped.append(Dropped(cand, cands[k], rho))
                break
        else:
            keep.append(i)
    cands = [cands[i] for i in keep]
    returns = [returns[i] for i in keep]

    vols, estimated = _volatilities(cands, returns)
    cov = _correlations(returns) * np.outer(vols, vols)
    liquidity = np.array([liquidity_score(c.market, c.side) for c in cands])
    budgets = np.array([c.relevance for c in cands]) * liquidity

    w = cap_weights(risk_budget_weights(cov, budgets), max_weight)
    marginal = cov @ w
    variance = float(w @ marginal)
    contributions = w * marginal / variance
    volatility = math.sqrt(variance)
    return Optimization(
        positions=[
            Weighted(c, float(w[i]), float(vols[i]), estimated[i], float(liquidity[i]), float(contributions[i]))
            for i, c in enumerate(cands)
        ],
        dropped=dropped,
        volatility=volatility,
        diversification_ratio=float(w @ vols) / volatility,
    )
