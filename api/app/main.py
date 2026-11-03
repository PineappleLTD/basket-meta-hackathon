import logging
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.brokers import Broker, BookSource, PaperBroker
from app.config import Settings
from app.db import SessionDep, init_db, make_engine
from app.kalshi import KalshiClient, MarketSource
from app.llm import LLM, MetaLlama
from app.models import AccountMode, Market
from app.routers import accounts, auth, baskets, markets, theses
from app.routers.common import sync_now
from app.services.sync import refresh_live

log = logging.getLogger(__name__)


def make_llm(settings: Settings) -> LLM | None:
    if not settings.meta_api_key:
        return None
    return MetaLlama(settings.meta_api_key, settings.llama_model, settings.llama_base_url)


def make_namer(settings: Settings) -> LLM | None:
    if not settings.meta_api_key:
        return None
    return MetaLlama(
        settings.meta_api_key, settings.llama_namer_model, settings.llama_base_url, reasoning_effort="minimal"
    )


def make_brokers(settings: Settings, books: BookSource) -> dict[AccountMode, Broker]:
    # Live trading registers a broker for AccountMode.LIVE here.
    paper = PaperBroker(
        settings.paper_fee_rate,
        books,
        max_quote_age=timedelta(minutes=settings.paper_max_quote_age_minutes),
        cache_seconds=settings.paper_quote_cache_seconds,
    )
    return {AccountMode.PAPER: paper}


def create_app(settings: Settings | None = None, market_source: MarketSource | None = None) -> FastAPI:
    """`market_source` replaces the live Kalshi API, e.g. with a fake in tests."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url, settings.shared_database)
        init_db(engine)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)

        app.state.sync_lock = threading.Lock()
        # time.monotonic() when the last sync started; None until one finishes.
        app.state.synced_at = None
        with app.state.sessionmaker() as session:
            has_markets = session.execute(select(Market.id).limit(1)).first() is not None
        app.state.market_source = market_source or KalshiClient(
            settings.kalshi_min_volume,
            settings.kalshi_exclude_categories,
            requests_per_second=settings.kalshi_requests_per_second,
        )
        app.state.llm = make_llm(settings)
        app.state.namer = make_namer(settings)
        app.state.brokers = make_brokers(settings, app.state.market_source)

        if settings.seed_on_startup and not has_markets:
            # An injected source seeds instantly; a live first sync takes about a minute,
            # so it runs in the background and the API serves an empty market list meanwhile.
            if market_source:
                _seed(app)
            else:
                threading.Thread(target=_seed, args=(app,), daemon=True).start()
        stop = threading.Event()
        if settings.live_refresh_seconds > 0:
            threading.Thread(target=_live, args=(app, stop), daemon=True).start()
        yield
        stop.set()
        engine.dispose()

    app = FastAPI(title="Prediction Market ETF API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(markets.router)
    app.include_router(baskets.router)
    app.include_router(theses.router)
    app.include_router(accounts.router)

    @app.get("/health", tags=["meta"])
    def health(session: SessionDep):
        """503 when the database can't be reached."""
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as e:
            raise HTTPException(503, "Database unavailable") from e
        return {"status": "ok"}

    return app


def _seed(app: FastAPI) -> None:
    with app.state.sync_lock:
        try:
            log.info("Seeded markets: %s", sync_now(app.state))
        except Exception:
            log.exception("Seeding markets failed; call POST /markets/sync to retry")


def _live(app: FastAPI, stop: threading.Event) -> None:
    """Re-price markets in use every live_refresh_seconds until `stop`. Skips a turn while a
    sync runs, so automations never run twice at once; the sync re-prices everything anyway."""
    while not stop.wait(app.state.settings.live_refresh_seconds):
        if not app.state.sync_lock.acquire(blocking=False):
            continue
        try:
            with app.state.sessionmaker() as session:
                counts = refresh_live(session, app.state.market_source, app.state.brokers)
            if counts["automated"]:
                log.info("Live refresh: %s", counts)
        except Exception:
            log.warning("Live refresh failed; trying again next interval", exc_info=True)
        finally:
            app.state.sync_lock.release()


app = create_app()
