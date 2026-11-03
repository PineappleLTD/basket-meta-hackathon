# SliceFund model and news agent

This directory bundles the `headline_model` and `news_agent` Python packages so
the ETF app can run from this repository without another local codebase.
Install it in editable mode from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-news.txt
```

Run the app suite from `api/` with `TEST_DATABASE_URL=sqlite:// ../.venv/bin/python -m pytest tests -q`.
Run package tests from here with `../.venv/bin/python -m pytest tests -q`.
Test directories are selected explicitly so archived/local copies are not collected.

The deployment model contains the original fitted StandardScaler and Ridge
parameters, with private training-report paths omitted. No retraining or final-set
evaluation occurred during packaging. `models/PROVENANCE.json` records the frozen
dataset and original/deployment artifact fingerprints plus exact prediction
parity on 107 synthetic feature probes. These probes test packaging parity; they
are not evidence of predictive accuracy. The artifact remains experimental.

The deployment pointer is relative to this directory, independent of the current
working directory. The original full artifact and private training dataset stay
outside Git. Do not load untrusted joblib/pickle artifacts.

Set `MUSE_DATA_DIR` to a private writable directory for the evidence cache and
news-agent state. Use the same directory for the API and agent. Without it, data
is created under this directory's ignored `state/` folder. No dataset, article
cache, account database, API credentials or private audit workbook is included.
The historical replay requires a separately configured private training workspace;
the ordinary package and API unit tests use disposable synthetic data.

News discovery currently supports configured RSS/Atom feeds and optional NewsAPI.
Meta/Muse discovery and evidence extraction require explicit call budgets. The news agent never receives trading tools.
See the repository's `INTEGRATION.md` for connection, execution controls and limits.
