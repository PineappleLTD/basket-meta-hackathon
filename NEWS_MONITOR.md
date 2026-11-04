# Background news monitoring

The API now owns the news worker: starting Basket's backend starts monitoring;
stopping it signals the worker to stop. Keep the API server running to monitor while
the browser is closed. The frontend does not hold API keys or start a worker per tab.

Install the root `requirements-news.txt` in the API's Python environment. Configure
the existing Meta server key through `META`, `PMETF_META_API_KEY`, or `MODEL_API_KEY`.
The packaged Ridge artifact and preprocessing are reused without retraining.

Every two minutes, the worker snapshots saved ETFs' open contracts and exact rules,
reads the configured RSS/Atom feeds, and selects fresh relevant records using the
existing LangChain agent. Muse extracts evidence; the saved Ridge model classifies
it. Basket independently validates the cached evidence before saving a signal.
Neither the agent nor this worker has order-placement tools.

The existing response setting remains authoritative:

- **Off:** news may still be read and displayed; no news-driven orders.
- **Suggest:** review a proposal before starting a paper rebalance.
- **Auto:** the existing price-update scheduler starts the paper rebalance.

Purchases wait for every planned sale to finish. Only existing ETF constituents and
held outcome sides receive proceeds. Completed allocations, including cash, remain
the saved ETF targets. Real exchange execution remains separately disabled by default.

## Spending controls

The initial allowance is **10 discovery HTTP attempts plus 10 evidence HTTP attempts
total**, shared across all ETFs and restarts. Each paid request is reserved in the
app database *before* sending; retries and failed attempts count. Polling does not
reset this allowance. Once either limit is reached, monitoring stops visibly.

RSS reads need no Muse credits and are cached for two minutes. Article identities,
discovery decisions and evidence are reused so the same supplied facts and contract
context do not need another paid assessment. A cycle can consider up to 20 source
records, make at most two discovery calls, and start at most three evidence
assessments, subject to the stricter shared totals. An evidence assessment may retry
up to three times; each attempt still counts against the evidence total.

These limits cover background news analysis. User-initiated thesis searches and
basket naming keep their existing separate API behavior.

| Setting | Default |
| --- | --- |
| `PMETF_NEWS_MONITOR_ENABLED` | `true` |
| `PMETF_NEWS_MONITOR_INTERVAL_SECONDS` | `120` (minimum 10) |
| `PMETF_NEWS_MONITOR_DISCOVERY_LIMIT` | `10`, cumulative |
| `PMETF_NEWS_MONITOR_EVIDENCE_LIMIT` | `10`, cumulative |
| `PMETF_NEWS_MONITOR_STATE` | `api/data/news-agent.sqlite3` |
| `PMETF_NEWS_FEATURE_CACHE` | packaged engine's data workspace evidence cache |
| `PMETF_NEWS_MODEL_PATH` | packaged active Ridge artifact |
| `PMETF_NEWS_MONITOR_RETRY_ERRORS` | `false` |

Increasing an allowance is an operator decision: configure an approved cumulative
limit and restart the backend. Do not reset/delete the usage table to replenish it.
Persist the app database, the agent state and the evidence cache across deployments.
SQLite workers share a lock based on the app database path. PostgreSQL workers share
a database advisory lock, including across hosts. Only one worker collects at a time;
other API processes stay available and can take over after the collector stops.

## Status and recovery

The News climate panel shows the collector's status, last completed check and
request usage. The owner-authenticated climate response includes the same
`monitoring` object without other owners' portfolio details.

- `MONITORING`: the last news check completed; the worker is waiting for its next poll.
- `WAITING_FOR_BASKETS`: no saved open contract has complete exchange rules yet.
- `WAITING_FOR_KEY` / `DISABLED`: configure the server key / enable monitoring.
- `DEGRADED`: some feeds failed; available feeds were checked.
- `BUDGET_EXHAUSTED`: no more background calls until an approved limit is configured.
- `PAUSED_ERROR`: a model, evidence, installation or configuration failure stopped
  monitoring. Existing app routes and price updates continue. Check configuration
  and provider access; then explicitly restart once with
  `PMETF_NEWS_MONITOR_RETRY_ERRORS=true`. Remove that override after recovery.
  The original request totals are preserved.
- `UNAVAILABLE`: the worker has not reported recently; check the backend process.

No paid-provider exception bodies, keys, or auth tokens are returned in status.
Invalid evidence produces no trading signal. This build performs paper execution;
the classifier's scores are directional evidence, not calibrated probabilities or
proof of profitable trading.

## Verification

`api/tests/test_news_monitor.py` uses local provider responses with the real
LangChain tooling, Meta evidence parser, saved Ridge model, evidence cache, signal
intake and existing paper scheduler. It verifies a 100-contract planned sale that
fills 40 first: replacement purchases wait until all 100 sell. Other tests cover
atomic persistent budgets, counted retries, duplicate workers, startup/shutdown,
provider failure isolation, stale status and changed constituents.

These tests spend no provider credits and place no real orders. Run them together
with the existing API and `news_engine/tests` regression suites. A passing local
test does not establish production deployment or real-exchange acceptance.
