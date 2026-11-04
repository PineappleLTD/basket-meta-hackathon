# Deploy Basket on Render

The repository deploys as one service: a production Next.js server proxies `/api/*`
to the Python API on loopback. The bundled model and news worker run in the same
container. Visitors use one HTTPS URL, and no browser requests point to localhost.

1. Create a Render Blueprint from `PineappleLTD/basket`, selecting branch
   `codex/render-deployment-20261104`. Render reads `render.yaml` and builds the
   root `Dockerfile` directly from this repository.
2. Review the configured service: Ohio region, `0.5c-512mb` compute, and a 1 GB
   persistent disk mounted at `/data`. The current starting cost is about
   **$7.25/month**: $7 compute plus $0.25 disk, before bandwidth, extra build
   usage, or Meta API calls. See [Render pricing](https://render.com/pricing) and
   [persistent disk pricing](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses).
3. Supply `META` in the private environment-variable prompt for thesis building
   and news monitoring. Do not add it to Git or any `NEXT_PUBLIC_*` variable.
4. Deploy the Blueprint. Render supplies the service's public HTTPS
   `onrender.com` URL. `/api/health` checks the database, and `/api/docs` serves
   the API documentation.

Accounts, baskets, paper holdings, market history, news state, evidence, and
request totals all live on the persistent disk. Keep this SQLite deployment at
one instance. Automatic deploys are off; publish later commits explicitly from
the Render dashboard. The Blueprint deploys the named branch without changing
`main`.

The initial deployment syncs the public market catalog and history before opening
the website, which can take several minutes. Later deployments reuse the disk
and skip that initial sync.
Startup enables SQLite WAL and waits for the API before starting the website.
Both processes shut down together so the host can restart a failed instance.

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

For a custom domain, add it in Render's service settings and create the DNS
records Render supplies. The generated Render HTTPS URL works without a domain.

## Railway alternative

`railway.json` remains available for deploying the same container on Railway.
Attach a volume at `/data`, provide `META` as a private variable, and generate
a public service domain. The launcher requires a mounted volume when it detects
a Railway environment.

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
