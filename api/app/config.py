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
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    # Meta Model API key (starts with LLM_). Read from META (unprefixed) or PMETF_META_API_KEY.
    meta_api_key: str | None = Field(default=None, validation_alias=AliasChoices("META", "PMETF_META_API_KEY"))
    llama_model: str = "muse-spark-1.3"
    llama_base_url: str = "https://api.meta.ai/v1"
