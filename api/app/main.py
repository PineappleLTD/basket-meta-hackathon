import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base, make_engine
from app.kalshi import KalshiClient, MarketSource
from app.llm import LLM, MetaLlama
from app.models import Market
from app.routers import auth, baskets, markets, theses
from app.services.sync import sync_markets

log = logging.getLogger(__name__)


def make_llm(settings: Settings) -> LLM | None:
    if not settings.meta_api_key:
        return None
    return MetaLlama(settings.meta_api_key, settings.llama_model, settings.llama_base_url)


def create_app(settings: Settings | None = None, market_source: MarketSource | None = None) -> FastAPI:
    """`market_source` replaces the live Kalshi API, e.g. with a fake in tests."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)

        app.state.sync_lock = threading.Lock()
        with app.state.sessionmaker() as session:
            has_markets = session.execute(select(Market.id).limit(1)).first() is not None
        app.state.market_source = market_source or KalshiClient(
            settings.kalshi_min_volume,
            settings.kalshi_exclude_categories,
            requests_per_second=settings.kalshi_requests_per_second,
        )
        app.state.llm = make_llm(settings)

        if settings.seed_on_startup and not has_markets:
            # An injected source seeds instantly; a live first sync takes about a minute,
            # so it runs in the background and the API serves an empty market list meanwhile.
            if market_source:
                _seed(app)
            else:
                threading.Thread(target=_seed, args=(app,), daemon=True).start()
        yield
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

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app


def _seed(app: FastAPI) -> None:
    with app.state.sync_lock, app.state.sessionmaker() as session:
        try:
            counts = sync_markets(session, app.state.market_source, app.state.settings.backfill_days)
            log.info("Seeded markets: %s", counts)
        except Exception:
            log.exception("Seeding markets failed; call POST /markets/sync to retry")


app = create_app()
