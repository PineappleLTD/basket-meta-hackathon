from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.auth import CurrentUser
from app.db import SessionDep
from app.llm import LLMError
from app.models import Market
from app.schemas import DroppedPick, MarketOut, PositionIn, ThesisIn, ThesisPick, ThesisSelection
from app.routers.common import sync_if_stale
from app.services import thesis as svc

router = APIRouter(prefix="/theses", tags=["theses"])


@router.post("/select", response_model=ThesisSelection)
def select_markets(body: ThesisIn, request: Request, session: SessionDep, _user: CurrentUser):
    """Pick weighted market positions that express a thesis. Creates nothing. Requires sign-in.

    Makes two model calls, so it takes several seconds. Syncs markets first when the last
    sync is older than PMETF_SYNC_MAX_AGE_MINUTES, which adds about ten seconds. Send
    `positions` to POST /baskets to build the basket.
    """
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(503, "No model API key configured (set META in .env)")
    sync_if_stale(request.app.state)
    if session.scalar(select(Market.id).limit(1)) is None:
        raise HTTPException(503, "Markets haven't been synced yet")
    try:
        selection = svc.select_markets(session, llm, body.thesis, body.max_positions, body.max_weight)
    except svc.ThesisError as e:
        raise HTTPException(422, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e

    opt = selection.optimization
    return ThesisSelection(
        thesis=body.thesis,
        summary=selection.summary,
        sub_themes=selection.sub_themes,
        keywords=selection.keywords,
        candidates_considered=selection.candidates_considered,
        picks=[
            ThesisPick(
                market=MarketOut.model_validate(w.candidate.market),
                side=w.candidate.side,
                weight=w.weight,
                relevance=w.candidate.relevance,
                rationale=selection.rationales[w.candidate.market.id],
                volatility=w.volatility,
                volatility_estimated=w.volatility_estimated,
                liquidity=w.liquidity,
                risk_contribution=w.risk_contribution,
            )
            for w in opt.positions
        ],
        dropped=[
            DroppedPick(
                market=MarketOut.model_validate(d.candidate.market),
                side=d.candidate.side,
                duplicate_of=d.duplicate_of.market.id,
                correlation=d.correlation,
            )
            for d in opt.dropped
        ],
        volatility=opt.volatility,
        diversification_ratio=opt.diversification_ratio,
        positions=[
            PositionIn(market_id=w.candidate.market.id, side=w.candidate.side, weight=w.weight)
            for w in opt.positions
        ],
    )
