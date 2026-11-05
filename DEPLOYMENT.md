# Deploy Basket for free with Render and Neon

The repository deploys as one Render Free service: a production Next.js server proxies `/api/*`
to the Python API on loopback. The bundled model and news worker run in the same
container. Visitors use one HTTPS URL, and no browser requests point to localhost.

1. Create or use a **Neon Free** PostgreSQL database. Use its direct connection
   string with pooling disabled; the collector holds a session-level advisory
   lock. Keep the connection string private.
2. Create a Render Blueprint from `PineappleLTD/basket`, selecting branch
   `codex/free-cloud-deployment-20261104`. Render reads `render.yaml` and builds
   the root `Dockerfile` directly from this repository.
3. Confirm **Free compute and no persistent disk**. Supply `PMETF_DATABASE_URL`
   with the Neon direct connection string and `META` with the server-side Meta
   key. These are private environment variables, never Git files or
   `NEXT_PUBLIC_*` values.
4. Deploy. Render supplies the public `onrender.com` HTTPS URL. `/api/health`
   checks the database, and `/api/docs` serves the API documentation.

The selected infrastructure is $0 within the providers' free allowances. Render
can suspend free services when monthly allowances are exhausted. Neon Free
currently provides 1 GB of PostgreSQL storage and 100 CU-hours per project per
month; compute is suspended when its monthly allowance is exhausted. Hosting
does not include the separate model provider's API usage.
[Render Free limits](https://render.com/docs/free), [Neon Free limits](https://neon.com/pricing)

Accounts, baskets, paper holdings, market history, signals, evidence, and request
totals live in PostgreSQL. With `PMETF_NEWS_DATABASE_STATE=true`, evidence is
committed there immediately. The collector's private SQLite working store is
checkpointed there after completed cycles and during orderly shutdown, then
restored before the next collector opens it. Only the collector holding the
instance lock restores or saves that store. An interrupted cycle may repeat
unfinished discovery, within the existing durable request allowance.

Render Free sleeps after 15 minutes without inbound traffic and takes about a
minute to wake. News monitoring and price refreshes run while the service is
awake, then resume when it wakes. This does not provide continuous unattended
monitoring. Keep one instance. Automatic deploys are off; publish later commits
explicitly from Render. This branch does not change `main`.

`BASKET_REQUIRE_POSTGRES=true` makes startup reject a missing hosted database
instead of silently saving user data on ephemeral local storage.

The initial deployment syncs the public market catalog and history before opening
the website, which can take several minutes. Later deployments reuse the database
and skip that initial sync.
Startup enables SQLite WAL and waits for the API before starting the website.
Both processes shut down together so the host can restart a failed instance.

Runtime starts briefly as root to assign its working directory to the `basket` user,
then drops privileges before opening databases or starting either server.

The image excludes local accounts, caches, `.env` files, and training data. A new
deployment starts with its own database; an existing production database is never
replaced or cleared. Import any local account data separately after deciding what
should move to production. Back up the database before changing or migrating it.

The default background allowance remains 30 discovery attempts and 30 evidence
attempts total, persisted across restarts. User-initiated thesis and naming calls
use the configured Meta account separately. Hosting this app does not enable real
exchange execution; the existing paper trading mode remains the default.

For a custom domain, add it in Render's service settings and create the DNS
records Render supplies. The generated Render HTTPS URL works without a domain.

## Railway alternative

`railway.json` remains available for deploying the same container on Railway.
Attach a volume at `/data`, provide `META` as a private variable, and generate
a public service domain. The launcher requires a mounted volume when it detects
a Railway environment. That alternative has separate pricing and is not the $0
configuration described above.

## Local production check

After `cd web && npm run build`, copy `.next/static` into
`.next/standalone/.next/static` (and `public` if present), then run from the root:

```sh
BASKET_DATA_DIR=/absolute/path/to/disposable-data \
PMETF_SEED_ON_STARTUP=false \
python scripts/run_production.py --host 127.0.0.1
```

This starts the compiled website with an empty local database and no market sync.
For normal production startup, leave `PMETF_SEED_ON_STARTUP` at its default.
To test the free-cloud storage path, provide `PMETF_DATABASE_URL`, set
`PMETF_NEWS_DATABASE_STATE=true`, and set `BASKET_REQUIRE_POSTGRES=true`.
