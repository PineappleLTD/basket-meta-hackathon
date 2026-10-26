# API

FastAPI + SQLAlchemy + SQLite backend for building "ETF" baskets of Kalshi outcomes.

## Run

```bash
cd api
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000/docs
.venv/bin/python -m pytest
```

The SQLite file lives at `api/data/app.db` and is created on startup; tables come from
`Base.metadata.create_all` (no migrations yet, so delete the file after schema changes).

Market data comes from Kalshi's public API, which needs no account or key. On first start
with an empty database the API syncs in the background (about a minute: ~17k markets plus 90
days of daily history for each); `/markets` is empty until it finishes. Later syncs take ~10s.

## Config

Env vars (or `.env` at the repo root / in `api/`), all prefixed `PMETF_`:

| Var                                | Default                                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------- |
| `PMETF_DATABASE_URL`               | `sqlite:///api/data/app.db`                                                   |
| `PMETF_SEED_ON_STARTUP`            | `true`                                                                        |
| `PMETF_BACKFILL_DAYS`              | `90` – daily history fetched for each newly seen market                       |
| `PMETF_KALSHI_MIN_VOLUME`          | `1000` – contracts traded                                                     |
| `PMETF_KALSHI_EXCLUDE_CATEGORIES`  | `["Sports"]`                                                                  |
| `PMETF_KALSHI_REQUESTS_PER_SECOND` | `4` – cap on Kalshi API requests; 10 drew 429s on a first sync                |
| `PMETF_PAPER_STARTING_CASH`        | `10000` – dollars a paper account opens and resets with                       |
| `PMETF_PAPER_FEE_RATE`             | `0.07` – Kalshi taker fee multiplier for paper fills                          |
| `PMETF_CORS_ORIGINS`               | `["http://localhost:3000", "http://localhost:5173"]`                          |
| `META` (or `PMETF_META_API_KEY`)   | unset – Meta Model API key (`LLM_…`); `/theses/select` returns 503 without it |
| `PMETF_LLAMA_MODEL`                | `muse-spark-1.3`                                                              |
| `PMETF_LLAMA_BASE_URL`             | `https://api.meta.ai/v1`                                                      |

## Auth

Email + password. `POST /auth/signup` or `/auth/login` returns a `token`; send it as
`Authorization: Bearer <token>`. Tokens last 30 days, and `POST /auth/logout` revokes the one
sent. Market reads and `/health` are public; everything else needs a token. Baskets belong to
the user who created them: other users get 404 for them, and names only need to be unique per
user.

Passwords are hashed with scrypt (stdlib). Only the SHA-256 of each token is stored, so the
database alone can't be used to sign in. Unknown emails and wrong passwords get the same 401
and take the same time.

## Paper trading

Each user gets a paper account (opened on first use with `$10,000`) and can put money into
their baskets. Accounts are per mode (`/accounts/paper`, `/accounts/live`); a mode works when a
broker is registered for it in `app.state.brokers`, and only paper has one so far (live → 501).

- **Buy** `$amount`: split by target weight; each leg buys the most whole contracts whose cost
  plus fee fits its share, so the total never exceeds the amount. Leftover cents stay as cash.
- **Sell** `fraction` (default 1 = everything): of every holding, rounded down. Legs with no bid
  (e.g. a closed market) stay held and are listed in `skipped`.
- **Rebalance**: trades the holdings back to target weights at their market value, sells first.
  Legs within 0.5 points of target are left alone, so repeat rebalances don't churn fees.
  This is separate from `POST /baskets/{id}/rebalance`, which resets the model index.

Paper fills are taker fills at the stored top of book: YES buys at `yes_ask`, YES sells at
`yes_bid`, NO buys at `1 - yes_bid`, NO sells at `1 - yes_ask`. Each fill pays Kalshi's fee,
`ceil_to_cent(0.07 × contracts × price × (1 − price))`. Limits: quotes are only as fresh as the
last sync, book depth is unknown so size never moves the price, and closed markets don't settle
yet (their holdings stay marked at the last price).

Everything is planned before anything is sent: a buy fails whole (409) if any leg has no quote,
or the amount exceeds cash or is too small to buy a contract. Pass `dry_run: true` to get the
planned orders, fees and cash impact without placing them. Holdings are marked at the market
price (mid), so a fresh buy shows a small loss from the spread and fees. A basket with holdings
can't be deleted (409); order history outlives it.

## Layout

- `app/kalshi/` – market data returning normalized `MarketData` through the `MarketSource`
  protocol (`fetch_markets`, `fetch_history`).
  - `client.py` – `KalshiClient`: pages `/events` with nested markets, keeps active markets
    over the volume threshold outside excluded categories. Price is the bid/ask mid when
    both sides are quoted, else the last trade. History comes from the batch candlesticks
    endpoint (daily, 100 tickers per request). Requests are paced to the configured rate; a
    429 doubles the gap between requests (honoring `Retry-After`) and successes ease it back.
    Tickers repeated across pages are kept once.
- `app/services/sync.py` – upserts markets on `external_id` (the Kalshi ticker), marks
  markets the source stopped returning as closed, backfills history for new markets (1000
  tickers at a time, at most one price per market per timestamp), and at
  one shared timestamp writes a `market_prices` row per market, a `basket_navs` row per
  basket and an `account_values` row per trading account.
- `app/services/baskets.py` – basket math: units are sized so NAV starts at 100; drift is
  current weight minus target; rebalancing resets units to targets at the current NAV.
- `app/llm.py` – `MetaLlama`: chat completions against the Meta Model API's OpenAI-compatible
  endpoint, asking for JSON matching a schema. Anything implementing `LLM.complete_json` can
  stand in (tests use a fake).
- `app/services/thesis.py` – thesis → positions. The model turns the thesis into sub-themes
  and search keywords; keywords score every open market's question (multi-word phrases count
  more, then volume breaks ties, at most 4 per event, near-certain prices excluded) to give up
  to 120 candidates; the model then picks from those by id, choosing YES/NO and a 0–1
  relevance. Unknown ids, duplicates, relevance < 0.3 and a third pick from one event are
  dropped, and the rest go to the optimizer.
- `app/services/optimize.py` – weights by risk budgeting: each position's risk budget is
  relevance × liquidity (bid/ask spread relative to price, and log volume), and weights are
  solved so each position's share of basket variance matches its share of the budget. Variance
  comes from daily returns on the held side over the last 90 days, with correlations shrunk
  toward 0 when history is short; markets with under 10 days of returns get a volatility
  estimated from their price. Positions whose returns correlate ≥ 0.85 with a more relevant
  pick are dropped as duplicates, and weights are capped at `max_weight` (default 0.25).
- `app/brokers.py` – the `Broker` protocol (`quote`, `fee`, `execute`) and `PaperBroker`, which
  fills in full at the stored top of book. A live Kalshi broker implements the same protocol.
- `app/services/trading.py` – sizing and planning buys, sells and rebalances against broker
  quotes, then booking fills to cash and holdings (average-cost basis, realized P&L on sells).
- `app/auth.py` – password hashing, sessions, and the `CurrentUser` dependency routes use to
  require sign-in.
- `app/routers/` – HTTP endpoints.

## Endpoints

🔒 = needs `Authorization: Bearer <token>`.

| Method       | Path                                      |                                                                                                                                                                                                                                                                                                                                                         |
| ------------ | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET          | `/health`                                 | runs `SELECT 1`; 503 if the database is unreachable                                                                                                                                                                                                                                                                                                     |
| POST         | `/auth/signup`                            | `{email, password}` (8+ chars) → `{token, user}`; 409 if the email is taken                                                                                                                                                                                                                                                                             |
| POST         | `/auth/login`                             | same body → `{token, user}`; 401 on wrong email or password                                                                                                                                                                                                                                                                                             |
| POST         | `/auth/logout`                            | ends the session for the token sent                                                                                                                                                                                                                                                                                                                     |
| GET          | `/auth/me`                                | the signed-in user                                                                                                                                                                                                                                                                                                                                      |
| GET          | `/markets`                                | `q`, `is_open`, `limit`, `offset`; sorted by volume                                                                                                                                                                                                                                                                                                     |
| GET          | `/markets/{id}`                           |                                                                                                                                                                                                                                                                                                                                                         |
| GET          | `/markets/{id}/prices`                    | YES price history, oldest first; `since`, `limit`                                                                                                                                                                                                                                                                                                       |
| POST         | `/markets/sync`                           | 🔒 pull from Kalshi; records prices and NAVs; 409 if a sync is running                                                                                                                                                                                                                                                                                  |
| GET / POST   | `/baskets`                                | 🔒 your baskets; create: `{name, description?, positions: [{market_id, side: yes\|no, weight?}]}`; omit all weights for equal weight                                                                                                                                                                                                                    |
| GET / DELETE | `/baskets/{id}`                           | 🔒 includes NAV, per-position value, weight, drift                                                                                                                                                                                                                                                                                                      |
| GET          | `/baskets/{id}/nav`                       | 🔒 NAV history (creation + every sync), oldest first; `since`, `limit`                                                                                                                                                                                                                                                                                  |
| GET          | `/baskets/{id}/rebalance?threshold=0.05`  | 🔒 preview trades                                                                                                                                                                                                                                                                                                                                       |
| POST         | `/baskets/{id}/rebalance`                 | 🔒 apply                                                                                                                                                                                                                                                                                                                                                |
| POST         | `/theses/select`                          | 🔒 `{thesis, max_positions?=10, max_weight?=0.25}` → picks with side, weight, relevance, rationale, volatility, liquidity and risk contribution; dropped duplicates; basket volatility and diversification ratio; plus `positions` ready for `POST /baskets`; creates nothing. 422 if nothing fits, 502 on model failure, 503 with no key or no markets |
| GET          | `/accounts/{mode}`                        | 🔒 `paper` (live → 501): cash, equity, P&L, holdings by basket with weight, drift and unrealized P&L                                                                                                                                                                                                                                                    |
| POST         | `/accounts/{mode}/reset`                  | 🔒 paper only: clears holdings, orders and history; restores starting cash                                                                                                                                                                                                                                                                              |
| GET          | `/accounts/{mode}/equity`                 | 🔒 equity history (opening, every trade, every sync), oldest first; `since`, `limit`                                                                                                                                                                                                                                                                    |
| GET          | `/accounts/{mode}/orders`                 | 🔒 basket orders with their per-market orders and fills, newest first; `basket_id`, `limit`                                                                                                                                                                                                                                                             |
| POST         | `/accounts/{mode}/baskets/{id}/buy`       | 🔒 `{amount, dry_run?}`                                                                                                                                                                                                                                                                                                                                 |
| POST         | `/accounts/{mode}/baskets/{id}/sell`      | 🔒 `{fraction?=1, dry_run?}`                                                                                                                                                                                                                                                                                                                            |
| POST         | `/accounts/{mode}/baskets/{id}/rebalance` | 🔒 `{dry_run?}`; 409 if already within tolerance                                                                                                                                                                                                                                                                                                        |
