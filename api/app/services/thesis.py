"""Turn a plain-English thesis into basket positions.

1. The model breaks the thesis into sub-themes and search keywords.
2. Keywords score every open market's question; the top scorers become candidates.
3. The model picks candidates that express the thesis, choosing YES or NO for each
   and rating relevance.
   Each candidate lists only the sides liquid enough to trade.
4. Picks are checked against the candidates, near-duplicates are dropped, and the
   rest are weighted by the optimizer (see optimize.py).

The model only ever chooses from real markets in the database, by id, so it can't
invent markets.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLM, LLMError
from app.models import Market, Side
from app.services.optimize import (
    DEFAULT_MAX_WEIGHT,
    Candidate,
    Optimization,
    liquidity_score,
    load_daily_prices,
    optimize,
)

MAX_CANDIDATES = 120
# Many events hold one market per outcome (every candidate, every rate bucket); cap
# them so one event can't crowd out the rest of the candidate list or the basket.
MAX_CANDIDATES_PER_EVENT = 4
MAX_POSITIONS_PER_EVENT = 2
# Near-certain markets carry no thesis exposure and can't be sized (see baskets.MIN_PRICE).
MIN_YES_PRICE, MAX_YES_PRICE = 0.02, 0.98
MIN_RELEVANCE = 0.3
# Sides scoring below this (see optimize.liquidity_score) aren't offered or kept. A wide
# spread on a cheap side, like YES on a 3c long shot, eats most of any gain.
MIN_LIQUIDITY = 0.2
# The model may pick this many extra markets, to fill slots freed by dropped duplicates.
SPARE_PICKS = 3


class ThesisError(ValueError):
    pass


@dataclass(frozen=True)
class Pick:
    market: Market
    side: Side
    relevance: float
    rationale: str


@dataclass(frozen=True)
class Selection:
    summary: str
    sub_themes: list[str]
    keywords: list[str]
    candidates_considered: int
    optimization: Optimization
    # Market id -> the model's one-line reason for the pick.
    rationales: dict[int, str]


_EXPAND_SYSTEM = """You are a prediction market analyst. Break the user's investment thesis \
into what would have to happen in the world for it to be right, and the words prediction \
market questions about those things would use.

Return JSON:
- summary: one sentence restating the thesis.
- sub_themes: 2-5 distinct sub-themes.
- keywords: 15-40 short search terms (1-3 words each) likely to appear verbatim in market \
titles: entity names (people, companies, agencies, countries, tickers), indicators \
("CPI", "unemployment", "rate cut"), events, and common synonyms. Lowercase."""

_EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "sub_themes": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "sub_themes", "keywords"],
}

_SELECT_SYSTEM = """You are a portfolio manager building a prediction market basket that \
pays off if the user's thesis is right. You are given candidate markets, one per line as \
`id | question | category | YES price | closes | sides`, where `sides` are the sides \
liquid enough to buy.

Pick up to {max_picks} markets whose outcome is meaningfully driven by the thesis. For each:
- side: "yes" if the thesis being right makes the market more likely to resolve YES, \
"no" if it makes NO more likely.
- relevance: 0-1, how directly the thesis drives this outcome.
- rationale: one sentence.

Prefer diversified exposure across the sub-themes over several near-duplicate markets. \
Don't pick two outcomes of the same event that contradict each other. Skip markets that \
are only loosely related; fewer, stronger picks beat padding.

Favor markets on broad, thesis-level outcomes (an annual record, a policy decision, an \
index level) over narrow single-event long shots (one named storm, one game). A few long \
shots are fine as upside, but most of the basket should be outcomes the thesis makes \
meaningfully more likely, not merely possible.

Only use ids from the list, and only a side listed under `sides`."""

_SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "market_id": {"type": "integer"},
                    "side": {"type": "string", "enum": ["yes", "no"]},
                    "relevance": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["market_id", "side", "relevance", "rationale"],
            },
        }
    },
    "required": ["picks"],
}


def _clean_keywords(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: dict[str, None] = {}
    for k in raw:
        if isinstance(k, str) and (k := " ".join(k.lower().split())) and len(k) >= 2:
            seen[k] = None
    return list(seen)


def _strings(raw: object) -> list[str]:
    return [s for s in raw if isinstance(s, str) and s.strip()] if isinstance(raw, list) else []


def tradeable_sides(market: Market) -> list[Side]:
    return [side for side in Side if liquidity_score(market, side) >= MIN_LIQUIDITY]


def find_candidates(session: Session, keywords: list[str], limit: int = MAX_CANDIDATES) -> list[Market]:
    """Open, sizeable markets with a tradeable side, ranked by keyword hits in the question, then volume.

    A multi-word keyword counts for more than a single word, since it's more specific.
    """
    patterns = [(re.compile(rf"\b{re.escape(k)}\b"), 1 + 0.5 * k.count(" ")) for k in keywords]
    markets = session.scalars(
        select(Market).where(
            Market.is_open,
            Market.yes_price >= MIN_YES_PRICE,
            Market.yes_price <= MAX_YES_PRICE,
        )
    ).all()

    scored = []
    for m in markets:
        text = f"{m.question} {m.category or ''}".lower()
        score = sum(w for p, w in patterns if p.search(text))
        if score and tradeable_sides(m):
            scored.append((score, m.volume, m))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    per_event: dict[str, int] = {}
    candidates = []
    for _, _, m in scored:
        key = m.event_ticker or m.external_id
        if per_event.get(key, 0) >= MAX_CANDIDATES_PER_EVENT:
            continue
        per_event[key] = per_event.get(key, 0) + 1
        candidates.append(m)
        if len(candidates) == limit:
            break
    return candidates


def _candidate_line(m: Market) -> str:
    closes = m.closes_at.date().isoformat() if isinstance(m.closes_at, datetime) else "?"
    sides = ",".join(tradeable_sides(m))
    return f"{m.id} | {m.question} | {m.category or '-'} | {m.yes_price:.2f} | {closes} | {sides}"


def _parse_picks(raw: object, candidates: list[Market]) -> list[Pick]:
    """Valid picks, most relevant first."""
    by_id = {m.id: m for m in candidates}
    picks: list[Pick] = []
    seen: set[int] = set()
    per_event: dict[str, int] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        market = by_id.get(item.get("market_id"))
        try:
            side = Side(str(item.get("side", "")).lower())
            relevance = min(max(float(item.get("relevance", 0)), 0.0), 1.0)
        except (ValueError, TypeError):
            continue
        if market is None or market.id in seen or relevance < MIN_RELEVANCE:
            continue
        if liquidity_score(market, side) < MIN_LIQUIDITY:
            continue
        event = market.event_ticker or market.external_id
        if per_event.get(event, 0) >= MAX_POSITIONS_PER_EVENT:
            continue
        seen.add(market.id)
        per_event[event] = per_event.get(event, 0) + 1
        picks.append(Pick(market, side, relevance, str(item.get("rationale", "")).strip()))

    picks.sort(key=lambda p: p.relevance, reverse=True)
    return picks


def _weigh(session: Session, picks: list[Pick], max_positions: int, max_weight: float) -> Optimization:
    """Drop duplicates across every pick first, so a dropped duplicate frees its slot."""
    cands = [Candidate(p.market, p.side, p.relevance) for p in picks]
    prices = load_daily_prices(session, [c.market.id for c in cands])
    first = optimize(cands, prices, max_weight)
    if len(first.positions) <= max_positions:
        return first
    kept = optimize([w.candidate for w in first.positions[:max_positions]], prices, max_weight)
    return Optimization(kept.positions, first.dropped, kept.volatility, kept.diversification_ratio)


def select_markets(
    session: Session, llm: LLM, thesis: str, max_positions: int, max_weight: float = DEFAULT_MAX_WEIGHT
) -> Selection:
    """Raises ThesisError when nothing fits, LLMError when the model call fails."""
    expanded = llm.complete_json(_EXPAND_SYSTEM, thesis, _EXPAND_SCHEMA)
    keywords = _clean_keywords(expanded.get("keywords"))
    if not keywords:
        raise LLMError("Model returned no search keywords")

    candidates = find_candidates(session, keywords)
    if not candidates:
        raise ThesisError("No open markets match this thesis")

    sub_themes = _strings(expanded.get("sub_themes"))
    user = "\n".join([
        f"Thesis: {thesis}",
        f"Sub-themes: {'; '.join(sub_themes) or '-'}",
        "",
        "Candidates:",
        *map(_candidate_line, candidates),
    ])
    chosen = llm.complete_json(_SELECT_SYSTEM.format(max_picks=max_positions + SPARE_PICKS), user, _SELECT_SCHEMA)
    picks = _parse_picks(chosen.get("picks"), candidates)
    if not picks:
        raise ThesisError("None of the matching markets express this thesis strongly enough")

    summary = expanded.get("summary")
    return Selection(
        summary=summary.strip() if isinstance(summary, str) else thesis,
        sub_themes=sub_themes,
        keywords=keywords,
        candidates_considered=len(candidates),
        optimization=_weigh(session, picks, max_positions, max_weight),
        rationales={p.market.id: p.rationale for p in picks},
    )
