"""Copy a quiesced Neon database into an empty private Oracle-hosted PostgreSQL.

Run on the target VM. Source is only read; restore is atomic and refuses a target
that already contains application tables. Credentials never appear in arguments.
"""

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import io
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit

IMAGE = "postgres:18.6-alpine"


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Expected raw KEY=value lines in the private environment file.")
            values[key.strip()] = value
    return values


def connection_environment(url):
    """Use libpq settings so pg_dump, psql and pg_restore connect identically."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("Expected a PostgreSQL connection URL.")
    settings = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": unquote(parsed.path[1:]),
        "PGCONNECT_TIMEOUT": "15",
    }
    for key, values in parse_qs(parsed.query).items():
        if key == "sslmode":
            settings["PGSSLMODE"] = values[-1]
        elif key == "channel_binding":
            settings["PGCHANNELBINDING"] = values[-1]
        else:
            raise ValueError("Unsupported database URL parameter; inspect connection settings privately.")
    return settings


def run_client(url, network, *command, backup_dir=None):
    settings = connection_environment(url)
    args = ["docker", "run", "--rm", "--network", network]
    for key in settings:
        args += ["--env", key]
    if backup_dir:
        args += ["--volume", f"{backup_dir}:/backups"]
    args += [IMAGE, *command]
    result = subprocess.run(args, env={**os.environ, **settings}, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"PostgreSQL {command[0]} failed; no credentials were printed. Inspect the target privately.")
    return result.stdout


def query(url, network, sql):
    output = run_client(url, network, "psql", "--no-psqlrc", "--csv", "--tuples-only",
                        "--set", "ON_ERROR_STOP=1", "--command", sql)
    return list(csv.reader(io.StringIO(output)))


def tables(url, network):
    return query(url, network,
                 "SELECT schemaname, tablename FROM pg_tables "
                 "WHERE schemaname IN ('public', 'shared') ORDER BY schemaname, tablename")


def counts(url, network, names):
    statements = []
    for schema, table in names:
        label = f"{schema}.{table}".replace("'", "''")
        identifier = '.'.join('"' + part.replace('"', '""') + '"' for part in (schema, table))
        statements.append(f"SELECT '{label}' AS name, count(*)::text AS rows FROM {identifier}")
    return dict(query(url, network, " UNION ALL ".join(statements))) if statements else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="/opt/basket/config")
    parser.add_argument("--backup-dir", default="/opt/basket/backups")
    parser.add_argument("--network", default="basket-oracle_default")
    parser.add_argument("--source-quiesced", action="store_true",
                        help="Confirm the old host is stopped and source writes are disabled")
    args = parser.parse_args()
    if not args.source_quiesced:
        parser.error("Stop source writes first, then pass --source-quiesced")
    directory = Path(args.config_dir).resolve()
    source = read_env(directory / "runtime.env").get("PMETF_DATABASE_URL", "")
    target = (directory / "database-url").read_text().strip()
    source_url, target_url = urlsplit(source), urlsplit(target)
    if not source_url.hostname or not source_url.hostname.endswith(".neon.tech"):
        parser.error("The source must be the existing Neon database")
    if parse_qs(source_url.query).get("sslmode") not in (["require"], ["verify-full"]):
        parser.error("The Neon source connection must require TLS")
    if target_url.hostname != "database" or target_url.path != "/basket":
        parser.error("The destination must be the private Compose basket database")
    if (directory / "local-postgres.enabled").exists():
        parser.error("The local database is already active; refusing to restore over it")
    if tables(target, args.network):
        parser.error("Destination already has application tables; refusing to overwrite it")
    source_tables = tables(source, args.network)
    if not source_tables or ["shared", "users"] not in source_tables:
        parser.error("Source does not contain the expected Basket schema")
    source_counts = counts(source, args.network, source_tables)
    backups = Path(args.backup_dir).resolve()
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backups / ("basket-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".dump")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    container_path = "/backups/" + backup.name
    run_client(source, args.network, "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
               "--schema=public", "--schema=shared", "--file=" + container_path, backup_dir=backups)
    run_client(target, args.network, "pg_restore", "--dbname=basket", "--exit-on-error",
               "--single-transaction", "--no-owner", "--no-privileges", container_path, backup_dir=backups)
    target_tables = tables(target, args.network)
    target_counts = counts(target, args.network, target_tables)
    if source_tables != target_tables or source_counts != target_counts:
        raise RuntimeError("Copy verification failed; the live target marker was not enabled.")
    with backup.open("rb") as archive:
        digest = hashlib.file_digest(archive, "sha256").hexdigest()
    descriptor = os.open(directory / "local-postgres.enabled", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as marker:
        marker.write(f"backup={backup.name}\nsha256={digest}\ntables={len(target_tables)}\n")
    print(f"Verified {len(target_tables)} tables and exact row counts. Source preserved. Backup: {backup.name}")
    print("Private PostgreSQL is selected for the next deploy; background workers remain staged until activation.")


if __name__ == "__main__":
    main()
