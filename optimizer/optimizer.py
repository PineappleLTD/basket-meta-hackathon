import numpy as np
import pandas as pd
import cvxpy as cp
 
 
def logit(p):
    p = np.clip(p, 0.01, 0.99)
    return np.log(p / (1 - p))
 
 
def build_covariance(basket, price_hist, shrink=0.2):
    """Covariance of $1 returns across contracts.
 
    price_hist: rows = time, columns = market_id, values = price of the CHOSEN side.
    For a No pick, pass 1 - yes_price so correlations have the right sign.
    """
    ids = basket["market_id"].tolist()
    n = len(ids)
    p = basket["price"].to_numpy()
    q = np.clip(p + basket["edge"].to_numpy(), 0.01, 0.99)
    sd = np.sqrt(q * (1 - q)) / p  # std of return per $ on a binary bet
 
    changes = logit(price_hist[ids]).diff().dropna()
    C = changes.corr().reindex(index=ids, columns=ids).fillna(0).to_numpy().copy()
    np.fill_diagonal(C, 1.0)
    C = (1 - shrink) * C + shrink * np.eye(n)  # ridge-style shrinkage for short histories
 
    Sigma = np.outer(sd, sd) * C
    return (Sigma + Sigma.T) / 2 + 1e-6 * np.eye(n)
 
 
def find_clusters(basket, price_hist, threshold=0.8):
    """Group contracts whose log-odds changes correlate above `threshold`:
    they are effectively the same bet. Returns lists of row indices (size >= 2)."""
    ids = basket["market_id"].tolist()
    C = logit(price_hist[ids]).diff().dropna().corr().reindex(index=ids, columns=ids).fillna(0).to_numpy()
    parent = list(range(len(ids)))
 
    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
 
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if C[i, j] > threshold:
                parent[root(j)] = root(i)
    groups = {}
    for i in range(len(ids)):
        groups.setdefault(root(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]
 
 
def optimize(basket, price_hist, w_prev=None, budget=10_000,
             cap=0.25, cluster_cap=0.3, cluster_threshold=0.8, liq_frac=0.02,
             risk_aversion=0.5, theme_weight=0.45, fee_weight=0.02, shrink=0.2,
             horizon_days=30):
    """basket columns: market_id, price (chosen side, 0-1, bid/ask mid), edge (q - p),
    relevance (0-1, from the LLM), volume (USD/day).
    Optional columns:
      spread         ask - bid on the chosen side (default 0)
      days_to_close  days until the market resolves (default horizon_days)
 
    cluster_cap: max combined weight for contracts that move together
    (correlation > cluster_threshold), so near-duplicates count as one bet.
    horizon_days: edge on contracts resolving within this many days counts in full;
    later ones are scaled by horizon_days / days_to_close (money locked up longer)."""
    n = len(basket)
    p = basket["price"].to_numpy()
    q = np.clip(p + basket["edge"].to_numpy(), 0.01, 0.99)
    rel = basket["relevance"].to_numpy()
    spread = basket["spread"].to_numpy() if "spread" in basket else np.zeros(n)
    days = basket["days_to_close"].to_numpy() if "days_to_close" in basket else np.full(n, horizon_days)
 
    # Spread: you buy at the ask (mid + half the spread), so part of the edge is lost on entry,
    # and every rebalance pays half the spread on top of the fee.
    half_spread = spread / 2
    trade_cost = fee_weight + half_spread / p  # cost per $ traded
 
    # Time: the same edge is worth less if it takes a year to pay out than a month.
    time_scale = horizon_days / np.maximum(days, horizon_days)  # 1 if soon, < 1 if far off
    mu = (q - p) / p * time_scale  # expected return per $, per unit of time
 
    Sigma = build_covariance(basket, price_hist, shrink)
    w_prev = np.zeros(n) if w_prev is None else np.asarray(w_prev, dtype=float)
    max_w = np.minimum(cap, liq_frac * basket["volume"].to_numpy() / budget)
 
    w = cp.Variable(n)
    objective = cp.Maximize(
        mu @ w
        + theme_weight * rel @ w
        - risk_aversion * cp.quad_form(w, cp.psd_wrap(Sigma))
        - trade_cost @ cp.abs(w - w_prev)
    )
    # sum <= 1: whatever caps and liquidity won't allow stays in cash
    constraints = [cp.sum(w) <= 1, w >= 0, w <= max_w]
    clusters = find_clusters(basket, price_hist, cluster_threshold)
    for g in clusters:
        constraints.append(cp.sum(w[g]) <= cluster_cap)
    prob = cp.Problem(objective, constraints)
    prob.solve()
    if prob.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Optimizer failed: {prob.status}")
 
    out = basket.copy()
    out["weight"] = np.round(np.clip(w.value, 0, None), 4)
    out["dollars"] = (out["weight"] * budget).round(2)
    out["cluster"] = ""
    for k, g in enumerate(clusters, 1):
        out.loc[out.index[g], "cluster"] = f"C{k}"
    out = out.sort_values("weight", ascending=False).reset_index(drop=True)
    out.attrs["cash"] = round(1 - out["weight"].sum(), 4)
    return out
 
 
def mock_data(seed=0, days=90):
    """'AI regulation' basket. eu_ai_act and eu_ai_delay are near-duplicates
    (move together), to show the optimizer refusing to double up."""
    rng = np.random.default_rng(seed)
    basket = pd.DataFrame({
        "market_id":     ["eu_ai_act", "eu_ai_delay", "us_ai_bill", "openai_fine", "chip_export"],
        "price":         [0.62, 0.60, 0.35, 0.20, 0.48],
        "edge":          [0.00, 0.00, 0.00, 0.00, 0.00],
        "relevance":     [0.95, 0.93, 0.90, 0.70, 0.60],
        "volume":        [400_000, 350_000, 250_000, 80_000, 300_000],
        "spread":        [0.01, 0.01, 0.02, 0.06, 0.02],  # openai_fine trades thin
        "days_to_close": [60, 60, 400, 30, 90],           # us_ai_bill settles in over a year
    })
    shocks = rng.normal(0, 0.15, (days, 5))
    shocks[:, 1] = 0.95 * shocks[:, 0] + 0.05 * shocks[:, 1]  # near-duplicate of eu_ai_act
    lo = logit(basket["price"].to_numpy()) + shocks.cumsum(axis=0) - shocks.cumsum(axis=0)[-1]
    hist = pd.DataFrame(1 / (1 + np.exp(-lo)), columns=basket["market_id"])
    return basket, hist
 
 
def compare(before, after, labels=("before", "after")):
    m = before[["market_id", "weight"]].merge(after[["market_id", "weight"]], on="market_id",
                                             suffixes=tuple(f"_{l}" for l in labels))
    m["change"] = (m[f"weight_{labels[1]}"] - m[f"weight_{labels[0]}"]).round(4)
    print(m.to_string(index=False))
 
 
if __name__ == "__main__":
    pd.set_option("display.width", 120)
    basket, hist = mock_data()
    cols = ["market_id", "price", "relevance", "spread", "days_to_close", "cluster", "weight", "dollars"]
 
    print("1) Initial basket (no edge yet: theme fit, risk, spread cost)")
    w0 = optimize(basket, hist)
    print(w0[cols].to_string(index=False))
    print(f"   cash: {w0.attrs['cash']:.2f}")
    pair = w0.set_index("market_id").loc[["eu_ai_act", "eu_ai_delay"], "weight"].sum()
    print(f"   eu_ai_act + eu_ai_delay: {pair:.2f} (near-duplicates, capped at 0.30 as one bet)")
 
    print("\n   Effect of spread (same basket with spread = 0 vs real spreads):")
    compare(optimize(basket.assign(spread=0.0), hist), w0, ("no_spread", "spread"))
 
    print("\n2) Headline: us_ai_bill and chip_export both underpriced by 8 points")
    basket2 = basket.copy()
    basket2.loc[basket2["market_id"].isin(["us_ai_bill", "chip_export"]), "edge"] = 0.08
    prev = w0.set_index("market_id").loc[basket2["market_id"], "weight"].to_numpy()
    w1 = optimize(basket2, hist, w_prev=prev)
    compare(w0, w1)
    print("   Same 8-point edge, but us_ai_bill resolves in 400 days vs 90 for chip_export,")
    print("   so its edge counts for less.")
 
    w1i = w1.set_index("market_id")["weight"]
    assert w1["weight"].sum() <= 1 + 1e-3 and (w1["weight"] <= 0.25 + 1e-4).all()
    assert w1i[["eu_ai_act", "eu_ai_delay"]].sum() <= 0.30 + 1e-4
    print("\nChecks passed: invested <= 100%, no contract above 25%, EU pair <= 30%.")
    