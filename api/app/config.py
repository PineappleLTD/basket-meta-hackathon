from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = API_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PMETF_",
        env_file=(REPO_DIR / ".env", API_DIR / ".env"),
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = f"sqlite:///{API_DIR / 'data' / 'app.db'}"
    # Users and basket definitions live in their own SQLite file (attached as schema `shared`),
    # small enough to commit. ":memory:" keeps them in memory.
    shared_database: str = str(API_DIR / "data" / "shared.db")
    # Sync on startup when the markets table is empty.
    seed_on_startup: bool = True
    # Days of daily price history fetched for each market the first time it's synced.
    backfill_days: int = 90
    # Live sync keeps active markets with at least this many contracts traded...
    kalshi_min_volume: float = 1_000
    # ...outside these event categories.
    kalshi_exclude_categories: list[str] = ["Sports"]
    # Cap on Kalshi API requests per second. Their basic tier nominally allows 20, but a
    # first sync's candlestick batches hit 429s at 10.
    kalshi_requests_per_second: float = 4.0
    # Cash a paper account opens (and resets) with, in dollars.
    paper_starting_cash: float = 10_000
    # Paper fills pay Kalshi's taker fee: ceil to the cent of rate * contracts * price * (1 - price).
    paper_fee_rate: float = 0.07
    # Paper orders fill against Kalshi's live book, fetched per trade and reused this long...
    paper_quote_cache_seconds: float = 15
    # ...and fall back to the last sync's top of book, if it's younger than this, when it can't be fetched.
    paper_max_quote_age_minutes: float = 30
    # Sync markets (settling resolved ones and running automations) this often in the
    # background. 0 turns it off; POST /markets/sync and sync.py still work.
    sync_interval_minutes: float = 15
    # POST /theses/select syncs first when the last sync is older than this, so a thesis sees
    # current markets even after the background sync was off or the host slept. 0 turns it off.
    sync_max_age_minutes: float = 15
    # Between syncs, re-price just the markets that baskets and holdings use, and run
    # automations, this often. About one Kalshi request per 50 markets. 0 turns it off.
    live_refresh_seconds: float = 10
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    # Meta Model API key (starts with LLM_). Read from META (unprefixed) or PMETF_META_API_KEY.
    meta_api_key: str | None = Field(default=None, validation_alias=AliasChoices("META", "PMETF_META_API_KEY"))
    llama_model: str = "muse-spark-1.3"
    llama_base_url: str = "https://api.meta.ai/v1"
    # Names saved baskets. Run at minimal reasoning so a save waits about a second.
    llama_namer_model: str = "muse-spark-1.1"
    # Classification is cache-only here. Extraction belongs to the separately budgeted news agent.
    news_model_path: str | None = None
    news_feature_cache: str | None = None
    # Never inferred from Meta credentials. External execution is off by default.
    live_enabled: bool = False
    live_environment: str = "demo"
    live_production_enabled: bool = False
    live_owner_id: int | None = None
    live_subaccount: int = Field(default=0, ge=0, le=63)
    live_key_id: str | None = None
    live_private_key_path: str | None = None
    live_max_order_cents: int | None = Field(default=None, gt=0)
    live_max_cycle_cents: int | None = Field(default=None, gt=0)
    live_max_daily_turnover_cents: int | None = Field(default=None, gt=0)
    live_max_fee_per_contract_cents: int | None = Field(default=None, ge=0)
    live_experimental_model_accepted: bool = False
