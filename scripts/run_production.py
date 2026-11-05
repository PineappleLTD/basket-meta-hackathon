"""Run the production website and API together with durable application storage."""

import argparse
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def runtime_environment():
    env = os.environ.copy()
    volume = env.get("RAILWAY_VOLUME_MOUNT_PATH")
    if env.get("RAILWAY_ENVIRONMENT_ID") and not volume:
        raise RuntimeError("Attach a Railway volume at /data before deploying Basket.")
    data = Path(volume or env.get("BASKET_DATA_DIR") or ROOT / "runtime-data").resolve()
    data.mkdir(parents=True, exist_ok=True)
    user = env.get("BASKET_RUNTIME_USER")
    if user and os.geteuid() == 0:
        import pwd

        account = pwd.getpwnam(user)
        os.chown(data, account.pw_uid, account.pw_gid)
        os.initgroups(user, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        # libpq and other clients resolve per-user files through HOME after dropping root.
        env["HOME"] = account.pw_dir
    env.setdefault("PMETF_DATABASE_URL", f"sqlite:///{data / 'app.db'}")
    env.setdefault("PMETF_SHARED_DATABASE", str(data / "shared.db"))
    env.setdefault("PMETF_NEWS_MONITOR_STATE", str(data / "news-agent.sqlite3"))
    env.setdefault("PMETF_NEWS_FEATURE_CACHE", str(data / "features.sqlite3"))
    env.setdefault("MUSE_DATA_DIR", str(data))
    env.setdefault("PMETF_CORS_ORIGINS", "[]")
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "api"), str(ROOT / "news_engine")))
    env["PYTHONUNBUFFERED"] = "1"
    env["NODE_ENV"] = "production"
    return env


def prepare_database(env):
    os.environ.update(env)
    sys.path[:0] = [str(ROOT / "api"), str(ROOT / "news_engine")]
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from app.config import Settings
    from app.db import init_db, make_engine
    from app.models import Market

    settings = Settings(_env_file=None)
    url = make_url(settings.database_url)
    if env.get("BASKET_REQUIRE_POSTGRES", "").lower() == "true" and url.get_backend_name() != "postgresql":
        raise RuntimeError("Configure PMETF_DATABASE_URL with the free hosted PostgreSQL database before deploying.")
    if settings.news_database_state and url.host and url.host.endswith(".neon.tech") and "-pooler." in url.host:
        raise RuntimeError("Use Neon's direct connection URL, with pooling disabled, for the collector's session lock.")
    if url.get_backend_name() == "sqlite":
        for filename in (url.database, settings.shared_database):
            if filename and filename != ":memory:":
                Path(filename).parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(filename, timeout=30) as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
    engine = make_engine(settings.database_url, settings.shared_database)
    try:
        init_db(engine)
        with Session(engine) as session:
            empty = session.scalar(select(Market.id).limit(1)) is None
        return settings.seed_on_startup and empty
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Use 127.0.0.1 for a local-only production check.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3000")))
    args = parser.parse_args()
    if args.port == 8000:
        raise RuntimeError("PORT must differ from the internal API port 8000.")
    server = ROOT / "web" / "server.js"
    if not server.is_file():
        server = ROOT / "web" / ".next" / "standalone" / "server.js"
    if not server.is_file():
        raise RuntimeError("Build the website before starting production: cd web && npm run build")
    env = runtime_environment()
    needs_seed = prepare_database(env)
    stopping = threading.Event()
    children = []

    def stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def start(command, cwd=ROOT, child_env=env):
        process = subprocess.Popen(command, cwd=cwd, env=child_env, start_new_session=True)
        children.append(process)
        return process

    try:
        if needs_seed:
            print("Preparing the initial market catalog before accepting visitors.", flush=True)
            seed = start([sys.executable, str(ROOT / "api" / "sync.py")], cwd=ROOT / "api")
            while seed.poll() is None and not stopping.wait(0.5):
                pass
            if stopping.is_set():
                return 0
            if seed.returncode:
                raise RuntimeError("Initial market sync failed; check the service logs and retry.")
            children.remove(seed)
        # First sync has finished; sign-in never competes with its long write transaction.
        env["PMETF_SEED_ON_STARTUP"] = "false"
        api = start([sys.executable, str(ROOT / "scripts" / "run_api.py"), "--root-path", "/api"])
        deadline = time.monotonic() + 60
        while not stopping.is_set():
            if api.poll() is not None:
                raise RuntimeError("The API exited before it was ready.")
            try:
                with urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("The API did not become healthy within 60 seconds.")
            stopping.wait(0.5)
        if stopping.is_set():
            return 0
        web_env = {**env, "PORT": str(args.port), "HOSTNAME": args.host}
        start(["node", str(server)], cwd=server.parent, child_env=web_env)
        while not stopping.wait(0.5):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A Basket service exited; restarting the deployment.")
        return 0
    finally:
        for child in reversed(children):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
