import math
import random
from datetime import date, timedelta

import numpy as np
import pytest

from app.models import Market, Side
from app.services.optimize import (
    Candidate,
    cap_weights,
    liquidity_score,
    optimize,
    risk_budget_weights,
    side_returns,
)

START = date(2026, 1, 1)


def _market(id, yes_price, bid=None, ask=None, volume=1_000_000):
    spread = 0.01
    return Market(
        id=id, external_id=f"M{id}", question=f"Market {id}", yes_price=yes_price, volume=volume,
        yes_bid=yes_price - spread / 2 if bid is None else bid,
        yes_ask=yes_price + spread / 2 if ask is None else ask,
    )


def _shocks(seed, days=60):
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(days)]


def _walk(start, vol, days=60, seed=0, shocks=None):
    """Daily YES prices following a log-odds walk. Reusing `shocks` makes walks move together."""
    shocks = shocks or _shocks(seed, days)
    z, prices = math.log(start / (1 - start)), {}
    for i, s in enumerate(shocks):
        z += vol * s
        prices[START + timedelta(days=i)] = 1 / (1 + math.exp(-z))
    return prices


def test_side_returns_mirror_for_no_and_skip_gaps():
    prices = {START: 0.4, START + timedelta(days=1): 0.5, START + timedelta(days=3): 0.6}
    yes = side_returns(prices, Side.YES)
    no = side_returns(prices, Side.NO)
    assert yes == {START + timedelta(days=1): pytest.approx(0.25)}
    assert no == {START + timedelta(days=1): pytest.approx(0.5 / 0.6 - 1)}


def test_liquidity_rewards_tight_spreads_and_volume():
    tight = _market(1, 0.5, bid=0.49, ask=0.51)
    wide = _market(2, 0.5, bid=0.40, ask=0.60)
    thin = _market(3, 0.5, bid=0.49, ask=0.51, volume=1_000)
    one_sided = _market(4, 0.5, bid=None, ask=0.51)
    one_sided.yes_bid = None
    assert liquidity_score(tight, Side.YES) > liquidity_score(wide, Side.YES)
    assert liquidity_score(tight, Side.YES) > liquidity_score(thin, Side.YES)
    assert liquidity_score(one_sided, Side.YES) < liquidity_score(tight, Side.YES)
    # The same 2c spread costs more relative to a cheap side.
    cheap = _market(5, 0.95, bid=0.94, ask=0.96)
    assert liquidity_score(cheap, Side.NO) < liquidity_score(cheap, Side.YES)


def test_risk_budget_weights_match_budgets():
    vols = np.array([0.1, 0.2, 0.4])
    corr = np.array([[1, 0.5, 0.2], [0.5, 1, 0.3], [0.2, 0.3, 1]])
    cov = corr * np.outer(vols, vols)
    budgets = np.array([1.0, 2.0, 1.0])
    w = risk_budget_weights(cov, budgets)
    contributions = w * (cov @ w) / (w @ cov @ w)
    assert w.sum() == pytest.approx(1)
    assert contributions == pytest.approx(budgets / budgets.sum(), abs=1e-6)

    # Uncorrelated with equal budgets: inverse volatility.
    w = risk_budget_weights(np.diag(vols**2), np.ones(3))
    assert w == pytest.approx((1 / vols) / (1 / vols).sum())


def test_cap_weights_redistributes_excess():
    w = cap_weights(np.array([0.7, 0.2, 0.1]), 0.4)
    assert w == pytest.approx([0.4, 0.4, 0.2])
    # A cap below 1/n can't be met; it relaxes to equal weight.
    assert cap_weights(np.array([0.9, 0.1]), 0.25) == pytest.approx([0.5, 0.5])


def test_long_shot_gets_less_weight_than_favorite():
    favorite, long_shot = _market(1, 0.8), _market(2, 0.04, bid=0.03, ask=0.05)
    prices = {1: _walk(0.8, 0.1, seed=1), 2: _walk(0.04, 0.1, seed=2)}
    opt = optimize([Candidate(long_shot, Side.YES, 0.9), Candidate(favorite, Side.YES, 0.9)], prices, max_weight=1)
    by_id = {w.candidate.market.id: w for w in opt.positions}
    assert by_id[2].volatility > by_id[1].volatility
    assert by_id[2].weight < 0.2 < 0.8 < by_id[1].weight


def test_duplicate_bets_are_dropped_keeping_the_more_relevant():
    shocks = _shocks(7)
    a, b, c = _market(1, 0.4), _market(2, 0.5), _market(3, 0.6)
    prices = {1: _walk(0.4, 0.1, shocks=shocks), 2: _walk(0.5, 0.1, shocks=shocks), 3: _walk(0.6, 0.1, seed=3)}
    # The same bet held from the other side is the opposite exposure, not a duplicate.
    opt = optimize([Candidate(b, Side.YES, 0.9), Candidate(a, Side.YES, 0.7), Candidate(c, Side.NO, 0.8)], prices)
    assert [w.candidate.market.id for w in opt.positions] == [2, 3]
    (drop,) = opt.dropped
    assert (drop.candidate.market.id, drop.duplicate_of.market.id) == (1, 2)
    assert drop.correlation > 0.95

    opposite = optimize([Candidate(b, Side.YES, 0.9), Candidate(a, Side.NO, 0.7)], prices)
    assert opposite.dropped == [] and len(opposite.positions) == 2


def test_correlated_pair_shares_risk_and_diversifies_less():
    shocks, noise = _shocks(9), _shocks(10)
    a, b, c = _market(1, 0.5), _market(2, 0.5), _market(3, 0.5)
    prices = {
        1: _walk(0.5, 0.1, shocks=shocks),
        # Partly the same shocks: correlated, but below the duplicate threshold.
        2: _walk(0.5, 0.1, shocks=[0.7 * s + 0.7 * n for s, n in zip(shocks, noise)]),
        3: _walk(0.5, 0.1, seed=11),
    }
    opt = optimize([Candidate(m, Side.YES, 1.0) for m in (a, b, c)], prices, max_weight=1)
    w = {p.candidate.market.id: p.weight for p in opt.positions}
    assert not opt.dropped
    assert w[3] > w[1] and w[3] > w[2]
    assert sum(p.risk_contribution for p in opt.positions) == pytest.approx(1)
    assert opt.diversification_ratio > 1


def test_missing_history_uses_estimated_volatility():
    measured, fresh = _market(1, 0.5), _market(2, 0.1)
    opt = optimize([Candidate(measured, Side.YES, 1), Candidate(fresh, Side.YES, 1)], {1: _walk(0.5, 0.1)})
    by_id = {w.candidate.market.id: w for w in opt.positions}
    assert not by_id[1].volatility_estimated and by_id[2].volatility_estimated
    # Scaled like the measured market, but riskier because it's cheaper: sqrt(9) / sqrt(1).
    assert by_id[2].volatility == pytest.approx(3 * by_id[1].volatility)
    assert sum(w.weight for w in opt.positions) == pytest.approx(1)


def test_no_history_at_all_still_weights():
    opt = optimize([Candidate(_market(i, p), Side.YES, 1) for i, p in [(1, 0.5), (2, 0.2)]], {}, max_weight=1)
    assert sum(w.weight for w in opt.positions) == pytest.approx(1)
    assert opt.positions[0].weight > opt.positions[1].weight
