# Basket on Azure for Students

The production backend moved to Azure on November 5, 2026 after Render's 512 MiB
limit caused repeated out-of-memory exits and Oracle's free ARM capacity was
unavailable. The shared deployment scripts remain in `deploy/oracle/`; they also
support this Ubuntu x64 host.

- Public app: https://basket-0d26fc05.swedencentral.cloudapp.azure.com
- Existing entry point: https://basket-live.onrender.com (307 redirect preserving
  the path and query string; the Render service runs only Caddy).
- Azure resource group: `basket-backend-rg`; VM: `basket-backend` in
  `swedencentral`, `Standard_B2as_v2`, 2 vCPUs and 8 GiB RAM.
- Release: `/opt/basket/releases/9f3a86f`, app image `basket:9f3a86f`.
- Runtime configuration: `/opt/basket/config`, root only; never print its contents.
- Docker project: `basket-oracle`. PostgreSQL 18 uses the persistent
  `postgres-data` volume and has no public port. Caddy owns public ports 80/443.

The verified database copy includes every table in `public` and `shared`.
The original Neon database and the private migration dump are preserved.
New writes go to PostgreSQL on this VM. Existing users sign in again at the new
origin; their accounts were copied. Real exchange execution remains disabled.

## Spending boundary

Use only the Azure for Students subscription, with **Spending limit: On**. Do not
upgrade to paid billing. The $100 credit is a total allowance, not a daily reset.
The checked Linux compute price was $0.0778/hour, approximately $56 per 30 days,
plus disk and public IP. The credit therefore supports roughly six to seven weeks
of continuous use, depending on other charges. Azure disables the subscription
when its credit is exhausted unless the account is upgraded.

See [Azure for Students](https://learn.microsoft.com/en-us/azure/education-hub/azure-dev-tools-teaching/azure-students-program)
and [spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit).

## Check and operate

Use Azure VM Run Command if the restricted SSH rule does not admit the operator's
current IP. Do not open SSH globally. From the release directory:

```sh
sudo docker compose --project-name basket-oracle \
  --env-file /opt/basket/config/compose.env \
  -f deploy/oracle/compose.yaml -f deploy/oracle/compose.postgres.yaml ps
python3 scripts/verify_deployment.py https://basket-0d26fc05.swedencentral.cloudapp.azure.com
```

`deploy/oracle/deploy.sh activate` starts the single set of background workers;
it requires `BASKET_PREVIOUS_WORKERS_STOPPED=true`. `stop-workers` leaves HTTP
serving enabled while disabling both schedulers. Both modes automatically select
the private database when `/opt/basket/config/local-postgres.enabled` exists.
Use the deploy script for app updates so worker settings remain explicit.

The migration's restore utility received a later fix, commit `66340e6`: its archive
restore list preserves PostgreSQL's existing `public` schema while restoring all
application data atomically. The fixed utility was installed on the VM and used
for the verified copy. The application image did not change.

## Back up and roll back

The VM disk now contains the live database. Take a fresh private backup before
updates, retain backups outside the VM as well, and periodically test restoration
into a separate database. A local custom-format backup can be made as root:

```sh
umask 077
docker exec basket-oracle-database-1 pg_dump -U postgres -d basket -Fc \
  > "/opt/basket/backups/basket-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Roll back application images while retaining the current database. Since writes
have moved to Azure, simply resuming Render against the old Neon database would
lose newer changes. A database rollback requires a write freeze and verified
reverse copy. Never remove Docker volumes or clear Neon as part of rollback.
