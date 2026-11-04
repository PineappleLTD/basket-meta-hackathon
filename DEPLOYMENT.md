# Deploy Basket on Railway

The repository deploys as one service: a production Next.js server proxies `/api/*`
to the Python API on loopback. The bundled model and news worker run in the same
container. Visitors use one HTTPS URL, and no browser requests point to localhost.

1. Create a Railway service from `PineappleLTD/basket`, using the repository root.
   Railway reads `railway.json` and builds the root `Dockerfile`.
2. Attach a persistent volume at `/data` **before the first deployment**. Accounts,
   baskets, paper holdings, market history, news state, evidence, and request totals
   live there. Use one replica with this SQLite configuration.
3. Add `META` as a private Railway environment variable for thesis building and
   news monitoring. Do not add it to Git or any `NEXT_PUBLIC_*` variable.
4. Generate a Railway public domain targeting the service's `PORT` (default 3000).
   The app and API are available at that HTTPS domain; `/api/health` checks the
   database, and `/api/docs` serves the API documentation.

The initial deployment syncs the public market catalog and history before opening
the website, which can take several minutes. The deployment health check allows
ten minutes. Later deployments reuse the volume and skip that initial sync.
Startup enables SQLite WAL and waits for the API before starting the website.
Both services shut down together and Railway restarts a failed deployment.

Runtime starts briefly as root to assign the mounted volume to the `basket` user,
then drops privileges before opening databases or starting either server.

The image excludes local accounts, caches, `.env` files, and training data. A new
deployment starts with its own database; an existing production volume is never
replaced or cleared. Import any local account data separately after deciding what
should move to production. Back up the volume before changing or migrating it.

The default background allowance remains 30 discovery attempts and 30 evidence
attempts total, persisted across restarts. User-initiated thesis and naming calls
use the configured Meta account separately. Hosting this app does not enable real
exchange execution; the existing paper trading mode remains the default.

For a custom domain, add it in Railway's networking settings and create the DNS
record Railway supplies. The generated Railway HTTPS URL works without a domain.

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
