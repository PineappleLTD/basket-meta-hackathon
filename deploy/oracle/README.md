# Oracle Always Free migration

This moves the existing Next.js/Python container to an Ubuntu ARM VM, stages it
against Neon, then copies the database into private PostgreSQL on the same VM.
The source database is preserved. Model files and provider settings are reused.
The Next.js API proxy timeout is five minutes for catalog refresh and model requests.

The private database avoids exhausting Neon's free compute allowance: a database
kept active by ten-second polling uses about 180 CU-hours per 30 days even at
0.25 CU, above the current 100 CU-hour free allowance.

## Free Tier boundary

Stay on an **unupgraded Oracle Free Tier account**. Do not select Pay As You Go or
upgrade to get around a capacity error. As checked November 4, 2026, the A1 allowance
for an Always Free tenancy totals **2 OCPUs and 12 GB RAM** across instances, with
**200 GB total boot/block storage**, in the home region. These limits include any
resources already in the account. The free trial's $300 is not the deployment budget.

Provision one `VM.Standard.A1.Flex` instance with **1 OCPU, 6 GB RAM**, Ubuntu 24.04
LTS ARM64, and the default 50 GB boot disk. Use an Always Free eligible Ubuntu image,
with no paid image license, backup policy, or other paid add-ons. Confirm the account
and aggregate allowances in the Console before creating it. If free capacity is
unavailable, leave the existing host intact and use the Azure student fallback.

Oracle requires card verification and may place a temporary authorization hold.
It says the card is not charged unless the account is upgraded. Always Free has no
uptime guarantee; idle compute can be reclaimed. This does not cover Meta API usage.

- [Oracle Free Tier and upgrade policy](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Current Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

## Host setup

1. Use the user's existing SSH public key, or a separately approved new key. Allow
   SSH only from the operator's IP. Allow public TCP 80 and 443 in the OCI network
   security rules. Keep 3000, 8000, and database ports private. Account creation,
   identity verification, billing details and acceptance of terms belong to the user.
2. Transfer a tracked-source release archive to `/opt/basket/releases/<commit>/`.
   Build on the ARM VM; the Node and Python base images support ARM64. Local runtime
   databases, `.env` files, caches and datasets are excluded by `.dockerignore`.
3. Run `sudo bash deploy/oracle/bootstrap-ubuntu.sh` on the new VM. It installs Docker
   from Docker's official Ubuntu apt repository and creates the configuration paths.
4. Store existing private values in `/opt/basket/config/runtime.env`, owned by root,
   mode `0600`. Use the **same direct Neon URL**, with `sslmode=require`, and the
   existing Meta key. Copy any other intentional provider/model settings from the old
   host. Never put private values in a release archive, cloud-init, Git, or logs.
5. Copy `compose.env.example` to `/opt/basket/config/compose.env` and set an immutable
   release image tag and public hostname. A domain's A record must point at the VM.
   For a no-domain demo, `basket.<public-IPv4>.sslip.io` resolves to that public IP
   without registration. Caddy provisions and renews HTTPS certificates. Verify HTTPS
   issuance before using the new endpoint; never bypass certificate warnings.

Only Caddy publishes host ports. The Python API stays on loopback in the app
container. The app limit is 4 GB, PostgreSQL 768 MB, and Caddy 128 MB. Stop the app
before a large rebuild if necessary to leave memory for the build. Docker restarts
services after a reboot. Logs rotate; database and collector/TLS files use named
persistent volumes. Protect and back up the VM: its disk is now the live database.

## Stage and verify

Run from the release root on the new VM:

```sh
sudo bash deploy/oracle/deploy.sh stage
python3 scripts/verify_deployment.py https://YOUR_HOSTNAME
```

Staging explicitly disables both news monitoring and live price/automation refresh.
It connects to the same database without running a second scheduler. Real exchange
execution remains off in every mode. The new process may initialize additive runtime
tables, as normal startup does; it does not replace user data.

Verify sign-in with an existing account, basket loading, market search, and a thesis
request. Watch memory during the request with `sudo docker stats --no-stream` and
inspect logs without printing environment values. Confirm the new service stays
responsive, completes a request beyond 30 seconds, and does not restart.

## Copy the database and cut over

Create private PostgreSQL credentials once, then start only the new database:

```sh
sudo python3 deploy/oracle/prepare-database.py
sudo docker compose --project-name basket-oracle \
  --env-file /opt/basket/config/compose.env \
  -f deploy/oracle/compose.yaml -f deploy/oracle/compose.postgres.yaml \
  up --detach --wait database
```

Schedule a short write freeze. Stop the old Render service completely, including
web requests and both schedulers, and stop the staged Oracle app and proxy:

```sh
sudo docker compose --project-name basket-oracle \
  --env-file /opt/basket/config/compose.env -f deploy/oracle/compose.yaml \
  stop caddy basket
sudo python3 deploy/oracle/copy-database.py --source-quiesced
```

The copy command accepts only a TLS Neon source and the private target. It refuses
a target containing application tables, saves a private custom-format dump in
`/opt/basket/backups`, restores atomically, and checks the table list and every
table's exact row count. It enables the local database only after verification.
It never clears Neon or prints connection secrets. Keep the write freeze in effect
until verification and cutover finish. If verification fails, inspect the target;
do not overwrite it or enable the marker manually.

Start the app against the verified private database with workers off, then verify:

```sh
sudo bash deploy/oracle/deploy.sh stop-workers
python3 scripts/verify_deployment.py https://YOUR_HOSTNAME
```

Confirm the database is healthy and existing accounts, baskets and thesis selection
work. Then activate the single new set of schedulers:

```sh
sudo BASKET_PREVIOUS_WORKERS_STOPPED=true bash deploy/oracle/deploy.sh activate
```

Update the published app link only after public HTTPS and authenticated flows pass.
The `onrender.com` hostname belongs to Render and cannot be pointed at Oracle with
DNS. A new hostname requires users to sign in again; saved accounts were copied.
An optional redirect at the old Render URL can preserve existing incoming links.

## Roll back

Before database cutover, stop Oracle's app and resume Render on the preserved Neon
source. After cutover, the two databases diverge as soon as Oracle accepts writes.
Do not resume the old database after that point: stop new writes, take a backup,
and plan a verified reverse copy or roll back only the application image while
keeping the new database. Never run `docker compose down --volumes` or clear Neon
as part of rollback. Retain the source and private dump until migration acceptance.

## Azure fallback

Use an unupgraded Azure for Students subscription if Oracle signup/capacity fails.
Select a Linux VM with at least 6 GB RAM for this configuration, and price the VM,
disk and public IP together before provisioning. The $100 credit lasts at most a
year and may be spent sooner. Keep the subscription spending limit enabled; services
stop when the credit is exhausted unless the subscription is upgraded. The same
container, private PostgreSQL, staged cutover, and verification apply.
