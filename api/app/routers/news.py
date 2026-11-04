"""Owner-scoped news evidence and paper rebalancing. Scores are never accepted from HTTP clients."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from app.auth import CurrentUser
from app.db import SessionDep
from app.models import AccountMode
from app.routers.common import owned_basket
from app.services import news, news_execution, trading

router = APIRouter(prefix="/baskets/{basket_id}/climate", tags=["news"])


class NewsSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["off", "suggest", "auto"]
    sensitivity: float = Field(gt=0, le=1)
    max_shift: float = Field(gt=0, le=1)


class ArticleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=1, max_length=2000)
    summary: str = Field(default="", max_length=12000)
    published_at: str
    url: str = Field(max_length=2000)
    source: str = Field(max_length=200)


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_id: str = Field(min_length=1, max_length=64)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle_id: str = Field(min_length=1, max_length=64)


def _not_locked(session, basket):
    from app.arb_models import lock_for
    if lock_for(session, basket.id):
        raise HTTPException(409, "This basket is a locked arbitrage: it's held to settlement, so news shifts are off")


def context(request, session, user, basket_id):
    basket = owned_basket(session, user, basket_id)
    account = trading.get_account(session, user, AccountMode.PAPER, request.app.state.settings.paper_starting_cash)
    broker = request.app.state.brokers.get(AccountMode.PAPER)
    return basket, account, broker


@router.get("")
def get_climate(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    basket, account, _ = context(request, session, user, basket_id)
    result = news.climate(session, basket, account)
    result["monitoring"] = request.app.state.news_monitor.status(session)
    return result


@router.get("/watchlist")
def get_watchlist(basket_id: int, session: SessionDep, user: CurrentUser):
    return news.watchlist(session, owned_basket(session, user, basket_id))


@router.get("/settings")
def get_settings(basket_id: int, session: SessionDep, user: CurrentUser):
    basket = owned_basket(session, user, basket_id)
    return news.settings_out(news.policy(session, basket))


@router.put("/settings")
def put_settings(basket_id: int, body: NewsSettingsIn, request: Request, session: SessionDep, user: CurrentUser):
    basket, account, broker = context(request, session, user, basket_id)
    if body.mode != "off":
        _not_locked(session, basket)
    item = news.policy(session, basket)
    for key, value in body.model_dump().items():
        setattr(item, key, value)
    session.add(item)
    # Turning off is effective immediately, including a partly filled cycle.
    cycle = news.active_cycle(session, account.id, basket.id)
    if body.mode == "off" and cycle:
        news_execution.advance(session, account, basket, broker, cycle)
    session.commit()
    return news.settings_out(item)


@router.post("/articles")
def post_article(basket_id: int, body: ArticleIn, request: Request, session: SessionDep, user: CurrentUser):
    basket = owned_basket(session, user, basket_id)
    predictor = getattr(request.app.state, "news_predictor", None)
    own = predictor is None
    try:
        if own:
            predictor = news.make_predictor(request.app.state.settings)
        result = news.ingest(session, basket, body.model_dump(), predictor)
        session.commit()
        return result
    except (ValueError, OSError) as error:
        session.rollback()
        # Provider bodies, file paths and keys never enter API errors.
        message = str(error) if isinstance(error, news.NewsError) else "News evidence could not be validated"
        raise HTTPException(422, message) from error
    finally:
        if own and predictor:
            predictor.close()


def _advance(basket_id, request, session, user, expected_id=None):
    basket, account, broker = context(request, session, user, basket_id)
    _not_locked(session, basket)
    cycle = None
    try:
        cycle = news.active_cycle(session, account.id, basket.id)
        if cycle is None:
            cycle = news_execution.start(session, account, basket, broker, expected_id)
            session.commit()
        news_execution.advance(session, account, basket, broker, cycle)
        session.commit()
        return news.cycle_out(cycle)
    except (news.NewsError, StaleDataError, OperationalError) as error:
        session.rollback()
        if isinstance(error, news.NewsError) and cycle is not None:
            failed = session.get(news.NewsCycle, cycle.id)
            if failed and failed.status in news.ACTIVE:
                failed.status, failed.note = "BLOCKED", str(error)
                session.commit()
        raise HTTPException(409, str(error) if isinstance(error, news.NewsError)
                            else "Account changed; refresh before retrying") from error


@router.post("/apply")
def apply(basket_id: int, body: ApplyIn, request: Request, session: SessionDep, user: CurrentUser):
    return _advance(basket_id, request, session, user, body.expected_id)


@router.post("/advance")
def advance(basket_id: int, request: Request, session: SessionDep, user: CurrentUser):
    basket, account, _ = context(request, session, user, basket_id)
    if news.active_cycle(session, account.id, basket.id) is None:
        raise HTTPException(409, "No pending news rebalance")
    return _advance(basket_id, request, session, user)


@router.post("/review")
def accept_current_allocation(basket_id: int, body: ReviewIn, request: Request, session: SessionDep, user: CurrentUser):
    """Explicitly keep current paper holdings/cash after a stopped cycle; leave news off."""
    basket, account, broker = context(request, session, user, basket_id)
    news_execution.lock_account(session, account)
    cycle = news.pending_review(session, account.id, basket.id)
    if cycle is None or cycle.id != body.cycle_id:
        session.rollback()
        raise HTTPException(409, "The pending review changed; refresh first")
    news.remember_allocation(session, account, basket)
    config = news.policy(session, basket)
    config.mode = "off"
    session.add(config)
    cycle.status, cycle.note = "REVIEWED", "Owner kept current holdings and cash; news response is off"
    session.commit()
    return news.cycle_out(cycle)
