# Basket news rebalancing integration

This integration is based on `PineappleLTD/basket-meta-hackathon` commit
`309f3a52f796eccb4096977b9bc1e9510b9058f4` and is delivered on a separate review
branch. The default branch is unchanged. Existing credentials are not used by the
feature; no new credentials or account databases are included in this change.

## Delivered implementation

The Ridge model, LangChain news bridge, paper workflow, authenticated Kalshi
adapter, durable external-order worker and app controls are built. Exchange
execution is **disabled by default**. Validation uses archived evidence, local
accounts, mocked exchange responses and a disposable PostgreSQL database.
No real-money orders or production-account tests were performed.

The repository now includes `news_engine/headline_model/`,
`news_engine/news_agent/`, and a trusted deployment copy of the trained model.
No separate local codebase is needed. Install from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-news.txt
```

Set `MUSE_DATA_DIR` to a private writable directory before starting both the API
and the agent. They must share this evidence-cache directory. API/web startup
otherwise follows the repository README. The original private training workspace
and full artifact remain separate and unchanged.

The deployment copy omits private training-report paths while retaining the exact
fitted preprocessing and Ridge parameters. `news_engine/models/PROVENANCE.json`
records both artifact fingerprints and exact prediction parity on 107 synthetic
feature probes. These probes check packaging, not predictive accuracy.

Extraction and Ridge fitting are complete: 6,204 valid cached assessments,
52 quarantined responses, and 2,400 related training rows from 419 story groups
used for fitting. The frozen November 1 revised dataset includes audit-driven
corrections. Michael confirmed personally auditing the dataset; the separate
Day 1 second-audit responses are preserved with their recorded disagreements and
uncertainty. Those limited responses do not establish dataset-wide predictive
accuracy. They were not silently applied to the frozen dataset after training.

Muse extracts evidence; it was not fine-tuned. Ridge uses its saved scaler and
coefficients. Scores are directional scores, not calibrated event probabilities.
The trained model remains experimental. Software tests establish the behavior
of the integration, not profitable future trading or perfect news judgments.

## Story to saved allocation

1. `api/news_bridge.py` exports the owner's existing ETF markets, held outcome
   sides and exact contract rules to the LangChain news agent. Discovery and
   evidence extraction require explicit budgets. The agent has no trading tools.
2. Muse evidence is stored in the content-addressed cache. The saved Ridge model
   classifies it. The bridge delivers articles; the app independently validates
   cached evidence and runs Ridge. Clients cannot supply classifications, order
   quantities or target weights. App intake makes no paid model calls.
3. Deterministic policy reduces accepted adverse positions. Recipients must
   already belong to the ETF and be held on the same YES/NO side. Saved movement
   caps apply. Unallocatable proceeds remain reserved ETF cash.
4. Immutable original sale goals are saved before execution. If only 40 of a
   planned 100 contracts sell, purchases wait for the other 60. Confirmed net
   proceeds after fees determine purchase budgets.
5. Holdings, individual fills, reserved cash and cycle progress are reconciled
   together. Reserved ETF cash cannot fund other baskets. Expiry, changed rules,
   changed holdings or new adverse recipient evidence stop new orders.
6. Only successful completion saves achieved market and cash weights for future
   deposits and rebalances. Unfinished cycles block ordinary basket automation.
   Owner review may explicitly keep confirmed holdings/cash and leave automation
   off; it never pretends an unfinished sale completed.

Repeated stories are deduplicated. Model abstentions, unrelated stories and
missing valid evidence cannot trigger a sale.

## External execution and recovery

`api/app/kalshi/execution.py` implements authenticated V2 orders, account snapshots,
order lookup, individual fill reconciliation and cancellation. YES/NO and buy/sell
are mapped to the venue's YES-price book. Orders are IOC; sells are reduce-only.
Requests use RSA-PSS/SHA256 or Ed25519 signatures over timestamp, method and path.
The adapter only uses the configured demo or production host and never forwards
credentials through redirects.

Saved execution scope includes a fingerprint of the exchange key ID. Changing
credentials cannot silently reuse a connected account or pending cycle; key
rotation requires an explicit account-binding review before execution resumes.

`api/app/services/exchange_execution.py` commits an immutable intent and stable
client ID before sending it. A database send claim permits one POST per intent.
A timeout or process crash leaves an unknown submission for client-ID lookup;
it never causes an automatic repeat POST. Confirmed individual fill IDs are
booked once. Terminal order evidence is refreshed before further trading.
Cancellation acknowledgements do not erase potential late fills.

Before each new intent, the complete exchange cash/position snapshot must match
the app. Mismatches, inconsistent evidence, rejected orders and spending-limit
violations stop new activity. Unresolved submissions cannot be dismissed by the
normal owner-review button. A never-visible uncertain submission requires an
operator to reconcile with venue records before any manual recovery; the app
does not guess that it failed.

Supported account scope: one owner-bound Kalshi subaccount and one connected ETF,
whole contracts, and cash/fees representable in cents. Fractional holdings,
sub-cent executions, opposite-side allocations on a netted account, outside
positions, or unbookable confirmed fills stop for accounting review. The latest
allowlisted fill evidence is retained. No partial batch is committed when a
confirmed batch cannot be booked safely.

Connection imports only allowed existing positions and cash and does not trade.
Cost basis starts at the connection mark; historical acquisition profit is not
invented. Public market settlement never fabricates live-account cash payouts;
real settlement changes must reconcile with venue/account records. The older
synchronous `/accounts/live` trading interface remains unavailable; external
news execution uses the dedicated `/exchange` workflow.

## Configuration and operation

The normal app configuration uses `PMETF_` environment variables. Keep secrets
outside Git. A Meta API key is for Muse; it cannot authenticate Kalshi orders.
Default `PMETF_LIVE_ENABLED=false` leaves the exchange panel off. To configure a
future demo deployment, supply all of these deliberately:

- `PMETF_LIVE_ENABLED=true`, `PMETF_LIVE_ENVIRONMENT=demo`
- `PMETF_LIVE_OWNER_ID`, `PMETF_LIVE_SUBACCOUNT` (0–63)
- `PMETF_LIVE_KEY_ID`, `PMETF_LIVE_PRIVATE_KEY_PATH` (Kalshi credentials)
- Positive `PMETF_LIVE_MAX_ORDER_CENTS`, `PMETF_LIVE_MAX_CYCLE_CENTS`,
  `PMETF_LIVE_MAX_DAILY_TURNOVER_CENTS`
- Nonnegative `PMETF_LIVE_MAX_FEE_PER_CONTRACT_CENTS`
- `PMETF_LIVE_EXPERIMENTAL_MODEL_ACCEPTED=true`

Limits conservatively count requested contracts at $1 each, including prior
prepared attempts over the last 24 hours; they are not promises of actual fees.
The fee buffer limits affordable quantities, and confirmed charges must still fit
reserved cash. Production additionally needs `PMETF_LIVE_ENVIRONMENT=production`
and `PMETF_LIVE_PRODUCTION_ENABLED=true`. These flags were not enabled here.

In the ETF's Exchange execution panel, connection alone leaves automation off.
The owner can start a proposed cycle or enable continued automation, inspect
confirmed progress, cancel residual orders, or review a fully reconciled stopped
cycle. Production activation has an additional visible confirmation. The normal
app scheduler advances the durable workflow; a dedicated paid worker is not
needed for order reconciliation. No test server or news bridge is left running
as part of the saved delivery.

For the news bridge, privately set `NEWS_APP_TOKEN` to the owner's app token and
run the shared interpreter with `api/news_bridge.py --basket-id <id>` while the
local API is running. Default operation delivers already-scored fresh stories.
`--interval <seconds>` repeats delivery. Discovery additionally requires
`--discover` and explicit positive `--requests`, `--model-calls`, `--score-calls`
and `--tool-calls` budgets. No paid calls were started by final verification.

## Code and checks

- `api/app/services/news.py`: evidence provenance, scope, deduplication, proposals.
- `api/app/services/news_execution.py`: shared immutable plan and paper executor.
- `api/app/kalshi/execution.py`: authenticated external transport and validation.
- `api/app/services/exchange_execution.py`: durable intent/fill state machine.
- `api/app/news_models.py`: additive evidence, cash, allocation and outbox tables.
- `api/app/services/trading.py`: shared ledger and reserved-cash accounting.
- `api/app/routers/news.py`, `exchange.py`: owner-scoped APIs.
- `web/components/ClimatePanel.tsx`, `ExchangePanel.tsx`: app controls/progress.
- `api/tests/test_news*.py`, `test_exchange*.py`: normal and failure-path tests.

From `api/`, run `TEST_DATABASE_URL=sqlite:// ../.venv/bin/python -m pytest tests -q`.
From `news_engine/`, run `../.venv/bin/python -m pytest tests -q`. From `web/`, run
`npm run typecheck` and `npm run build` (a cold build may download the app's
existing Google Fonts).

The shared workspace's `state/feature-completion/verify_postgres.py` creates a
fresh disposable local PostgreSQL database, runs the same suite, verifies RLS
and non-owner read isolation, and removes its temporary server. It does not
contact production Supabase.

The canonical `scripts/verify_training.py` validates the frozen dataset hash,
original groups, 6,204 cached evidence records, saved preprocessing and model
fingerprint without calling Meta or evaluating final labels again.

This copy's `scripts/replay_archived_news.py` uses an accepted archived TRAIN
story, actual cached evidence and saved Ridge prediction with synthetic prices
and fills in an in-memory ETF. It demonstrates 40/100 sold with no buys, then
100/100 with no buys, then existing-recipient purchases and saved allocation.
It is an engineering replay, not a historical profit/liquidity backtest.

Local validation passed 200 API tests on SQLite and disposable PostgreSQL,
76 original package tests, TypeScript, the frontend production build, and a
browser simulation of the full sale/purchase sequence with weights saved across
reload. Packaging adds tests for relative model loading and private-path removal.
Real exchange acceptance, credentials/account deployment and real-money testing
remain separate activation work.

Primary venue contracts used for this implementation:
[create V2 order](https://docs.kalshi.com/api-reference/orders/create-order-v2),
[order direction](https://docs.kalshi.com/getting_started/order_direction),
[authentication](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests),
[orders](https://docs.kalshi.com/api-reference/orders/get-orders),
[fills](https://docs.kalshi.com/api-reference/portfolio/get-fills),
[cancel V2](https://docs.kalshi.com/api-reference/orders/cancel-order-v2).
