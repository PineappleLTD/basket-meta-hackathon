# API

FastAPI + SQLAlchemy + SQLite backend for building "ETF" baskets of Kalshi outcomes.

## Run

```bash
cd api
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000/docs
.venv/bin/python -m pytest
```

For app-managed news monitoring, install `requirements-news.txt` from the repository
root into the same Python environment used by the API. See [NEWS_MONITOR.md](../NEWS_MONITOR.md)
for startup, the shared request allowance, monitoring status, and recovery.

The SQLite file lives at `api/data/app.db` and is created on startup; tables come from
`Base.metadata.create_all`. The news monitor adds tables without replacing existing data;
do not delete an existing database to enable it.
Users, baskets and basket positions are kept apart in `api/data/shared.db` (attached as
schema `shared`), which is small and committed to git. Positions name their market by Kalshi
ticker, since market ids differ between databases. Sessions, NAV history and trading stay in
`app.db`, so signing in and syncing don't change the shared file.

Market data comes from Kalshi's public API, which needs no account or key. On first start
with an empty database the API syncs in the background (about a minute: ~17k markets plus 90
days of daily history for each); `/markets` is empty until it finishes. Later syncs take ~10s.
To sync without logging in, run `.venv/bin/python sync.py` from `api/`; it uses the same settings.

## Config

Env vars (or `.env` at the repo root / in `api/`), all prefixed `PMETF_`:

| Var                                | Default                                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------- |
| `PMETF_DATABASE_URL`               | `sqlite:///api/data/app.db`                                                   |
| `PMETF_SHARED_DATABASE`            | `api/data/shared.db` – SQLite file for users and baskets                      |
| `PMETF_SEED_ON_STARTUP`            | `true`                                                                        |
| `PMETF_BACKFILL_DAYS`              | `90` – daily history fetched for each newly seen market                       |
| `PMETF_KALSHI_MIN_VOLUME`          | `1000` – contracts traded                                                     |
| `PMETF_KALSHI_EXCLUDE_CATEGORIES`  | `["Sports"]`                                                                  |
| `PMETF_KALSHI_REQUESTS_PER_SECOND` | `4` – cap on Kalshi API requests; 10 drew 429s on a first sync                |
| `PMETF_PAPER_STARTING_CASH`        | `10000` – dollars a paper account opens and resets with                       |
| `PMETF_PAPER_FEE_RATE`             | `0.07` – Kalshi taker fee multiplier for paper fills                          |
| `PMETF_PAPER_QUOTE_CACHE_SECONDS`  | `15` – how long a fetched order book is reused (preview, then the order)      |
| `PMETF_PAPER_MAX_QUOTE_AGE_MINUTES`| `30` – oldest stored quote paper fills fall back to when Kalshi is unreachable |
| `PMETF_CORS_ORIGINS`               | `["http://localhost:3000", "http://localhost:5173"]`                          |
| `META` (or `PMETF_META_API_KEY`, `MODEL_API_KEY`) | unset – server-side Meta key; `/theses/select` returns 503 without it |
| `PMETF_LLAMA_MODEL`                | `muse-spark-1.3`                                                              |
| `PMETF_LLAMA_BASE_URL`             | `https://api.meta.ai/v1`                                                      |
| `PMETF_LLAMA_NAMER_MODEL`          | `muse-spark-1.1` – titles baskets saved without a name, at minimal reasoning  |

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

- **Buy** `$amount`: split by the index's *current* weights (what its NAV is made of now), so the
  holding moves with NAV. Each leg buys the most whole contracts its share affords, walking the
  book, fees included; leftover cash then buys one more contract for the legs furthest under
  their share. The total never exceeds the amount.
- **Sell** `fraction` (default 1 = everything): of every holding, rounded down, into the bids.
- **Rebalance**: resets the basket's index to its targets (closed markets leave it and their
  weight goes to the rest) and trades the holdings to match, sells first, funded by the sells
  and then cash. Open holdings the basket no longer targets are sold. Legs within 0.5 points of
  target are left alone, and holdings in closed markets wait to settle.
- **Settlement**: each sync looks up results for closed markets a basket or holding uses. A
  resolved market takes its payout as price (so the index holds it like cash and NAV doesn't
  jump), and holdings in it are paid out to cash with no fee, as a `settle` basket order.

Paper fills walk Kalshi's live order book (`/markets/orderbooks`, fetched per trade and cached
for `PMETF_PAPER_QUOTE_CACHE_SECONDS`): YES buys lift NO bids at `1 − p`, YES sells hit YES bids,
and the reverse for NO. Each order's limit is the deepest level it was sized against, so if the
book thinned since the preview it fills partly; fills use up the cached liquidity. If the book
can't be fetched, the stored top of book stands in (unknown depth) while it's younger than
`PMETF_PAPER_MAX_QUOTE_AGE_MINUTES`. Each fill pays Kalshi's fee,
`ceil_to_cent(0.07 × Σ contracts × price × (1 − price))`. Resting maker orders aren't simulated:
paper can't know queue position.

Everything is planned before anything is sent. Legs that can't trade (closed, no bids or asks,
stale quote, a thin book) are listed in `skipped` and the rest go ahead; a buy's skipped share
stays as cash. 409 when nothing at all can trade, cash is short, or the amount buys nothing.
Pass `dry_run: true` to get the planned orders with average, best and limit price, price impact,
fees and cash impact without placing them.

Money that changes hands (cash, cost basis, fees, realized P&L) is whole cents: buys round up,
sells round down. Accounts carry a version number, so two trades planned against the same
balance can't both commit; the loser gets 409 and nothing is traded.

Holdings are marked at the market price (mid, else last trade, or the payout once resolved), so
a fresh buy shows a loss from the spread and fees. `liquidation_value` is what selling now at the
best bids would bring after fees. Each basket holding is unitized: the first buy issues shares at
the NAV, and later money in or out (buys, sells, payouts) moves shares at the holding's own
value per share. `share_value` is directly comparable to the NAV, and `holding_return` vs
`index_return` shows what trading costs and rounding took. A basket with holdings can't be
deleted (409); order history outlives it.

**Automations** run after every sync (`/theses/select`, `POST /markets/sync`, or `sync.py`) and every live refresh, each
committed on its own, with the outcome in the stake's `note`:

- auto-rebalance once any holding drifts more than `auto_rebalance_drift` from target;
- recurring buys of `recurring_amount` every `recurring_days` (missed runs aren't made up);
- conditional orders that fire once the basket NAV is `at_or_below` / `at_or_above` a level:
  limit buys, stop-losses and take-profits. They fill at market; one that can't is `failed`.

## Arbitrage scanner

Groups of Kalshi markets whose prices break a rule of probability, each fixed by a set of
contracts (one of every leg) that pays a floor whatever happens:

- **Strike ladders** (`ladder`): "above 4%" can't resolve YES unless "above 3.75%" does, so YES
  on the lower strike plus NO on the higher pays at least $1. Both markets must be one-sided
  thresholds in the same event, the event can't be mutually exclusive, and their rules must
  match once each market's own strike is blanked. (One event can hold unrelated goals, and
  Kalshi labels "exactly N" buckets as `less` with floor = cap; neither is a ladder.)
- **Deadline pairs** (`time`): "by June" implies "by December". Both must be cumulative (they
  close early when the event happens, and their rules give a window), their rules must match
  apart from dates and times, and the later one's window must start no later than the earlier
  one's (an explicit "from <date>" in the rules, else when the market opened).
- **Brackets** (`bracket_no`, `bracket_yes`): at most one market of a mutually exclusive event
  wins, so NO on k of them pays at least k − 1 (the subset with the best return is used). When
  every one of the event's markets is synced and their ranges leave no value uncovered, YES on
  all of them pays exactly $1.

Each sync stores every market's strike, event and rule templates (`market_terms`), then
`refresh` scans: every group is screened at the stored top of book, the 60 best within a cent
of paying are priced against the live order books with Kalshi's fees, and each is sized at
the number of sets that makes the most money. What pays is upserted into
`arb_opportunities` (first/last seen, times seen, best profit), and what no longer pays is
marked inactive, so the table is the benchmark; `arb_scans` logs every pass.

**Locks** (paper only) buy one contract of every leg per set into a basket marked in
`lock_baskets`. They re-price against the live books first (409 if the gap closed or a size
doesn't pay), check cash, and buy every leg; if one fills short, the others' extra contracts
are sold back so the legs match, and if one fills nothing, nothing is locked. Lock baskets
refuse buys by weight, rebalances (holdings or index), partial sells, automations,
conditional orders, news shifts and exchange connection; selling everything unwinds them, and
settlement pays the floor out as usual.

**Implications** across events ("a recession in 2027" and "the Fed cuts") need a model to read
the questions, so they run on demand: the model proposes pairs among markets matching a query,
and code checks the price rule (the implied market can't be the less likely one). Pairs priced
backwards are logged as `implication` opportunities with `guaranteed: false` and can't be locked.

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
  basket and an `account_values` row per trading account. It also looks up results for closed
  markets in use and pays out holdings in resolved ones. `refresh` runs a sync and then the
  automations; the API runs it before every `/theses/select`.
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
- `app/brokers.py` – the `Broker` protocol (`books`, `fee_cents`, `execute`), order `Book`s that
  walk levels, and `PaperBroker`, which fills against Kalshi's live book. A live Kalshi broker
  implements the same protocol.
- `app/services/trading.py` – sizing and planning buys, sells and rebalances against broker
  books, booking fills to cash and holdings (average-cost basis, realized P&L), shares per
  stake, and paying out resolved markets.
- `app/services/automation.py` – conditional orders, recurring buys and auto-rebalancing, run
  after each sync.
- `app/services/arbitrage.py` – the arbitrage scanner: market terms from each sync, screening,
  sizing against order books, the opportunity log, locks, and model-proposed implications.
  `app/arb_models.py` holds its tables, additive so existing databases need no migration.
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
| POST         | `/markets/sync`                           | 🔒 pull from Kalshi; records prices and NAVs, settles resolved markets, runs automations and the arbitrage scan; 409 if a sync is running                                                                                                                                                                                                                                                                                  |
| GET / POST   | `/baskets`                                | 🔒 your baskets; create: `{name?, description?, positions: [{market_id, side: yes\|no, weight?}]}`; omit all weights for equal weight; omit `name` to save as "New basket"                                                                                                                                                                                                                    |
| GET / DELETE | `/baskets/{id}`                           | 🔒 includes NAV, per-position value, weight, drift                                                                                                                                                                                                                                                                                                      |
| GET          | `/baskets/{id}/nav`                       | 🔒 NAV history (creation + every sync), oldest first; `since`, `limit`                                                                                                                                                                                                                                                                                  |
| GET          | `/baskets/{id}/rebalance?threshold=0.05`  | 🔒 preview trades                                                                                                                                                                                                                                                                                                                                       |
| POST         | `/baskets/{id}/rebalance`                 | 🔒 apply                                                                                                                                                                                                                                                                                                                                                |
| POST         | `/baskets/{id}/title`                     | 🔒 rename to a short model-written title (about a second); falls back to the description |
| POST         | `/theses/select`                          | 🔒 `{thesis, max_positions?=10, max_weight?=0.25}` → picks with side, weight, relevance, rationale, volatility, liquidity and risk contribution; dropped duplicates; basket volatility and diversification ratio; plus `positions` ready for `POST /baskets`; creates nothing. 422 if nothing fits, 502 on model failure, 503 with no key or no markets |
| GET          | `/accounts/{mode}`                        | 🔒 `paper` (live → 501): cash, equity, P&L, holdings by basket with weight, drift and unrealized P&L                                                                                                                                                                                                                                                    |
| POST         | `/accounts/{mode}/reset`                  | 🔒 paper only: clears holdings, orders and history; restores starting cash                                                                                                                                                                                                                                                                              |
| GET          | `/accounts/{mode}/equity`                 | 🔒 equity history (opening, every trade, every sync), oldest first; `since`, `limit`                                                                                                                                                                                                                                                                    |
| GET          | `/accounts/{mode}/orders`                 | 🔒 basket orders with their per-market orders and fills, newest first; `basket_id`, `limit`                                                                                                                                                                                                                                                             |
| POST         | `/accounts/{mode}/baskets/{id}/buy`       | 🔒 `{amount, dry_run?}`                                                                                                                                                                                                                                                                                                                                 |
| POST         | `/accounts/{mode}/baskets/{id}/sell`      | 🔒 `{fraction?=1, dry_run?}`                                                                                                                                                                                                                                                                                                                            |
| POST         | `/accounts/{mode}/baskets/{id}/rebalance` | 🔒 `{dry_run?}`; also resets the index to its targets; 409 if already within tolerance                                                                                                                                                                                                                                                                  |
| GET          | `/accounts/{mode}/baskets/{id}/history`   | 🔒 the holding's value and value per share after every trade and sync, with the NAV at each point                                                                                                                                                                                                                                                      |
| GET / PUT    | `/accounts/{mode}/baskets/{id}/automation`| 🔒 `{auto_rebalance_drift?, recurring_amount?, recurring_days?, recurring_start?}`; omitted fields turn off                                                                                                                                                                                                                                              |
| GET          | `/accounts/{mode}/conditional-orders`     | 🔒 newest first; `basket_id`, `status`                                                                                                                                                                                                                                                                                                                   |
| POST         | `/accounts/{mode}/baskets/{id}/conditional-orders` | 🔒 `{action, amount, condition: at_or_below\|at_or_above, nav}`; amount is dollars for a buy, a 0–1 share for a sell; 409 if the NAV is already there                                                                                                                                                                                         |
| DELETE       | `/accounts/{mode}/conditional-orders/{id}`| 🔒 cancel; 409 if it already fired                                                                                                                                                                                                                                                                                                                      |
| GET          | `/arbitrage`                              | 🔒 open opportunities (most profit first), implications checked in the last day, and the benchmark summary: scans, opportunities logged, profit open now, your locks |
| POST         | `/arbitrage/scan`                         | 🔒 `{sync?=false}`; scan now (about a second), or sync Kalshi first (about 30s, which scans) |
| POST         | `/arbitrage/implications`                 | 🔒 `{query}` → model-proposed pairs with relation, confidence and price check; logs pairs priced backwards. 422 if under 2 markets match, 502 on model failure, 503 with no key |
| GET          | `/accounts/{mode}/arbitrage/locks`        | 🔒 your lock baskets with cost, guaranteed payout, profit, and `status` (`locked`, `settled`, `closed`) |
| POST         | `/accounts/{mode}/arbitrage/{id}/lock`    | 🔒 paper only: `{sets?, dry_run?}`; without `sets`, the most profitable size; a second lock of the same opportunity adds to its basket. 409 if it no longer pays, the books can't fill it, cash is short, or the opportunity isn't guaranteed |
