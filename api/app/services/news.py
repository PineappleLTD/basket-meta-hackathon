"""Cached Muse/Ridge evidence and deterministic proposals over existing ETF holdings.

This service never calls Meta. The news agent extracts evidence with its explicit
budget; this app independently reads and validates that evidence from the cache.
"""
from datetime import datetime, timedelta
import hashlib
import json
import math
import re
from pathlib import Path

from sqlalchemy import select

from app.models import Account, AccountMode, Side, as_utc, utcnow
from app.news_models import NewsCash, NewsConsumption, NewsContract, NewsCycle, NewsPolicy, NewsSignal

ACTIVE = ("SELLING", "BUYING")
MAX_NEWS_AGE = timedelta(hours=1)


def utc(value):
    return as_utc(datetime.fromisoformat(value)) if isinstance(value, str) else as_utc(value)


class NewsError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def policy(session, basket):
    found = session.get(NewsPolicy, basket.id)
    return found or NewsPolicy(basket_id=basket.id, mode="suggest", sensitivity=0.5, max_shift=0.05)


def settings_out(item):
    return {"mode": item.mode, "sensitivity": item.sensitivity, "max_shift": item.max_shift}


def constituents(basket):
    return sorted((p.market.external_id, p.side.value) for p in basket.positions)


def reserved_cash(session, account_id):
    return sum(session.scalars(select(NewsCash.cents).where(NewsCash.account_id == account_id)))


def basket_cash(session, account_id, basket_id):
    return session.scalar(select(NewsCash.cents).where(NewsCash.account_id == account_id, NewsCash.basket_id == basket_id)) or 0


def active_cycle(session, account_id, basket_id):
    return session.scalar(select(NewsCycle).where(
        NewsCycle.account_id == account_id, NewsCycle.basket_id == basket_id, NewsCycle.status.in_(ACTIVE)
    ))


def pending_review(session, account_id, basket_id):
    return session.scalar(select(NewsCycle).where(
        NewsCycle.account_id == account_id, NewsCycle.basket_id == basket_id,
        NewsCycle.status.in_(("BLOCKED", "EXPIRED", "CANCELLED"))
    ).order_by(NewsCycle.created_at.desc()).limit(1))


def blocks_automation(session, account_id, basket_id):
    return active_cycle(session, account_id, basket_id) or pending_review(session, account_id, basket_id)


def cycle_out(cycle):
    if not cycle:
        return None
    return {"id": cycle.id, "status": cycle.status, "note": cycle.note,
            "updated_at": as_utc(cycle.updated_at).isoformat(),
            "sells": cycle.payload.get("sells", []), "buys": cycle.payload.get("buys", []),
            "proceeds_cents": cycle.payload.get("proceeds_cents", 0),
            "spent_cents": cycle.payload.get("spent_cents", 0)}


def remember_allocation(session, account, basket, now=None):
    """Save achieved positions plus reserved cash only after a cycle completes."""
    from app.news_models import NewsAllocation
    from app.services.baskets import value_basket
    values = {(h.market_id, h.side.value): h.contracts * h.market.price_for(h.side)
              for h in account.holdings if h.basket_id == basket.id}
    cash = basket_cash(session, account.id, basket.id) / 100
    total = sum(values.values()) + cash
    if total <= 0:
        return
    nav = value_basket(basket).nav
    allocation = session.get(NewsAllocation, basket.id)
    if allocation is None:
        allocation = NewsAllocation(basket_id=basket.id)
        session.add(allocation)
    allocation.cash_weight = cash / total
    allocation.cash_units = nav * allocation.cash_weight
    allocation.updated_at = now or utcnow()
    for position in basket.positions:
        weight = values.get((position.market_id, position.side.value), 0) / total
        position.target_weight = weight
        price = position.market.price_for(position.side)
        position.units = nav * weight / price if price > 0 else 0.0
    basket.rebalanced_at = now or utcnow()


def watchlist(session, basket):
    topics, missing = [], []
    for p in basket.positions:
        spec = session.get(NewsContract, p.market_id)
        if not spec or not spec.rules.strip() or not p.market.closes_at:
            missing.append(p.market.external_id)
            continue
        contract = {"platform": "kalshi", "market_id": p.market.external_id,
                    "outcome": p.side.value.upper(), "question": p.market.question,
                    "resolution_rules": spec.rules, "deadline": as_utc(spec.deadline).isoformat(),
                    "thesis": basket.description or ("The supplied resolution condition occurs." if p.side == Side.YES
                                                       else "The supplied resolution condition does not occur.")}
        stop = {"will", "have", "does", "what", "which", "before", "after", "than", "this", "that", "year", "more", "less", "above", "below"}
        aliases = list(dict.fromkeys(w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", p.market.question)
                                     if w.casefold() not in stop))
        topics.append({"id": f"basket-{basket.id}-{p.market_id}-{p.side.value}", "category": p.market.category or "General",
                       "alias_groups": [aliases or [p.market.question]], "query": p.market.question[:500],
                       "contract": contract, "market_id": p.market_id})
    return {"topics": topics, "missing_rules": missing}


class CacheOnlyClient:
    def __init__(self, model):
        self.model = model

    def assess(self, *args, **kwargs):
        raise NewsError("No validated cached evidence for this exact article and contract")


def make_predictor(settings):
    from headline_model.predictor import Predictor
    from headline_model.ridge_model import PositionRidge
    artifact = settings.news_model_path
    if artifact is None:
        from headline_model.paths import MODEL_ROOT
        artifact = Path(json.loads((MODEL_ROOT / "models" / "active.json").read_text())["artifact"])
        if not artifact.is_absolute():
            artifact = MODEL_ROOT / artifact
    scorer = PositionRidge(artifact)
    return Predictor(settings.news_model_path, cache_path=settings.news_feature_cache, max_calls=1,
                     client=CacheOnlyClient(scorer.evidence_model))


def ingest(session, basket, article_data, predictor, now=None):
    from headline_model.records import Article
    now = now or utcnow()
    article = Article.parse(article_data)
    if not article.is_fresh(now):
        raise NewsError("Article is stale or future-dated")
    article_dict = dict(article.__dict__)
    saved, skipped = [], []
    topics = watchlist(session, basket)
    for topic in topics["topics"]:
        contract = topic["contract"]
        key = digest({"basket": basket.id, "article": article_dict, "contract": contract,
                      "artifact": predictor.artifact_hash})
        old = session.get(NewsSignal, key)
        if old:
            saved.append(key)
            continue
        try:
            result = predictor.predict(article_dict, contract, now=now)
        except NewsError:
            skipped.append({"market_id": topic["market_id"], "reason": "missing_cached_evidence"})
            continue
        # Historical replay is never an executable signal; classification comes from our predictor.
        if result.get("historical") or result.get("artifact_sha256") != predictor.artifact_hash:
            raise NewsError("Untrusted inference provenance")
        if result.get("reason") == "model_call_budget_reached":
            skipped.append({"market_id": topic["market_id"], "reason": "missing_cached_evidence"})
            continue
        session.add(NewsSignal(id=key, basket_id=basket.id, market_id=topic["market_id"],
                              side=contract["outcome"].lower(), published_at=utc(article.published_at),
                              payload={"article": article_dict, "result": result,
                                       "contract_hash": digest(contract)}))
        saved.append(key)
    session.flush()
    return {"saved": saved, "skipped": skipped, "missing_rules": topics["missing_rules"]}


def available_signals(session, basket, account=None, now=None):
    now = now or utcnow()
    consumed = set(session.scalars(select(NewsConsumption.signal_id).where(
        NewsConsumption.account_id == account.id, NewsConsumption.basket_id == basket.id
    ))) if account else set()
    consumed_stories = set()
    for sid in consumed:
        prior = session.get(NewsSignal, sid)
        if prior:
            consumed_stories.add((prior.market_id, prior.side,
                                 " ".join(prior.payload["article"]["headline"].casefold().split())))
    specs = {t["market_id"]: t["contract"] for t in watchlist(session, basket)["topics"]}
    signals = session.scalars(select(NewsSignal).where(
        NewsSignal.basket_id == basket.id, NewsSignal.published_at >= now - MAX_NEWS_AGE
    ).order_by(NewsSignal.published_at.desc())).all()
    unique, result = set(), []
    for sig in signals:
        spec = specs.get(sig.market_id)
        if not spec or sig.id in consumed or spec["outcome"].lower() != sig.side:
            continue
        if utc(spec["deadline"]) <= now or sig.payload["contract_hash"] != digest(spec):
            continue
        # Republishing the same headline cannot amplify its pressure.
        story = (sig.market_id, sig.side, " ".join(sig.payload["article"]["headline"].casefold().split()))
        if story in unique or story in consumed_stories:
            continue
        unique.add(story)
        result.append(sig)
    return result


def proposal(session, account, basket, now=None):
    now = now or utcnow()
    config = policy(session, basket)
    allowed = {(p.market_id, p.side.value) for p in basket.positions}
    held = {(h.market_id, h.side.value): h for h in account.holdings if h.basket_id == basket.id and h.contracts > 0}
    if held.keys() - allowed:
        raise NewsError("Holdings no longer match this ETF's constituent list; review them first")
    if any(not math.isfinite(h.market.price_for(h.side)) or not 0 < h.market.price_for(h.side) <= 1 for h in held.values()):
        raise NewsError("A held position has an unusable mark; review it before rebalancing")
    signals = available_signals(session, basket, account, now)
    values = {key: h.contracts * h.market.price_for(h.side) for key, h in held.items()}
    total = sum(values.values())
    if not math.isfinite(total) or total <= 0:
        return {"id": digest({"basket": basket.id, "empty": True}), "shifts": [], "signal_ids": [], "reason": "No holdings to rebalance"}
    baseline = {key: value / total for key, value in values.items()}
    pressures, ids = {}, {}
    for key in held:
        touching = [s for s in signals if (s.market_id, s.side) == key]
        accepted = [s for s in touching if s.payload["result"].get("classification") in ("ADVERSE", "FAVORABLE", "NEUTRAL")]
        pressures[key] = sum({"ADVERSE": -1, "NEUTRAL": 0, "FAVORABLE": 1}[s.payload["result"]["classification"]]
                             for s in accepted) / max(len(accepted), 1)
        ids[key] = [s.id for s in accepted]
    target = baseline.copy()
    cuts = {k: min(config.max_shift, w * -pressures[k] * config.sensitivity)
            for k, w in baseline.items() if pressures[k] < 0 and held[k].market.is_open}
    for key, cut in cuts.items():
        target[key] -= cut
    remaining = sum(cuts.values())
    # Capped redistribution among existing, held sides only. Unallocatable weight stays cash.
    recipients = {k for k in held if pressures[k] >= 0 and held[k].market.is_open and k not in cuts}
    while recipients and remaining > 1e-12:
        denom = sum(baseline[k] for k in recipients)
        assigned = 0.0
        for key in sorted(recipients):
            add = min(remaining * baseline[key] / denom, config.max_shift - (target[key] - baseline[key]))
            target[key] += add
            assigned += add
        remaining -= assigned
        recipients = {k for k in recipients if target[k] - baseline[k] < config.max_shift - 1e-12}
        if assigned <= 1e-12:
            break
    shifts = [{"market_id": key[0], "side": key[1], "current_weight": baseline[key],
               "proposed_weight": max(0, target[key]), "pressure": pressures[key], "signal_ids": ids[key],
               "contracts": held[key].contracts, "mark": held[key].market.price_for(held[key].side)} for key in sorted(held)]
    signal_ids = sorted({sid for key in cuts for sid in ids[key]})
    body = {"account": account.id, "basket": basket.id, "constituents": constituents(basket),
            "shifts": shifts, "signal_ids": signal_ids, "settings": settings_out(config)}
    return {"id": digest(body), "event_key": digest(signal_ids), "shifts": shifts,
            "signal_ids": signal_ids, "cash_weight": max(0, remaining), "value": total,
            "reason": None if signal_ids else "No new accepted adverse evidence"}


def climate(session, basket, account, now=None):
    now = now or utcnow()
    signals = available_signals(session, basket, now=now)
    rows = []
    for sig in signals:
        a, r = sig.payload["article"], sig.payload["result"]
        direction = {"ADVERSE": -1, "FAVORABLE": 1}.get(r.get("classification"), 0)
        rows.append({"id": sig.id, "headline": a["headline"], "source": a["source"], "url": a["url"],
                     "published_at": a["published_at"], "topic": "Held-position evidence",
                     "sentiment": direction * (1 if sig.side == "yes" else -1),
                     "confidence": r.get("confidence", 0), "market_ids": [sig.market_id],
                     "classification": r.get("classification"), "reason": r.get("abstention_reason", r.get("reason")),
                     "experimental": r.get("experimental", True)})
    latest = session.scalar(select(NewsCycle).where(NewsCycle.account_id == account.id, NewsCycle.basket_id == basket.id)
                            .order_by(NewsCycle.created_at.desc()).limit(1))
    latest = active_cycle(session, account.id, basket.id) or pending_review(session, account.id, basket.id) or latest
    try:
        plan = proposal(session, account, basket, now)
    except NewsError as error:
        plan = {"id": "blocked", "shifts": [], "reason": str(error)}
    return {"regime": "shifting" if any(s["sentiment"] for s in rows) else "calm",
            "intensity": sum(abs(s["sentiment"]) for s in rows) / max(len(rows), 1),
            "summary": f"{len(rows)} cached assessments. Experimental model; paper trading only.",
            "as_of": now.isoformat(), "topics": [], "signals": rows, "proposal": plan,
            "settings": settings_out(policy(session, basket)), "cycle": cycle_out(latest),
            "basket_cash": basket_cash(session, account.id, basket.id) / 100,
            "missing_rules": watchlist(session, basket)["missing_rules"], "execution_mode": "paper"}
