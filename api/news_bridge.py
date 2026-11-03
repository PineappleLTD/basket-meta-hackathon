"""Connect the installed SliceFund news agent to the local basket app.

Default operation delivers already-scored fresh articles only. --discover needs
explicit request, Muse and tool budgets; no background process is started here.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


class AppBridge:
    def __init__(self, base_url, token, basket_id, *, client=None):
        parts = urlsplit(base_url)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError("Use a loopback HTTP app origin, without credentials in its URL")
        if not token.strip() or basket_id < 1:
            raise ValueError("A signed-in app token and basket id are required")
        self.base_url, self.basket_id = base_url.rstrip("/"), basket_id
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.path = f"/baskets/{basket_id}/climate"

    def _request(self, method, path, **kwargs):
        result = self.client.request(method, self.base_url + path, headers=self.headers, **kwargs)
        if result.status_code >= 300:
            raise ValueError(f"Local basket app returned HTTP {result.status_code}")
        return result.json()

    def watchlist(self):
        result = self._request("GET", self.path + "/watchlist")
        if not result.get("topics"):
            raise ValueError("The ETF has no scoreable contracts; sync exact market rules first")
        return result

    def deliver(self, store, topics, *, limit=40, now=None):
        from datetime import datetime, timezone
        now = now or datetime.now(timezone.utc)
        topic_ids = {t["id"] for t in topics}
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS app_deliveries (key TEXT PRIMARY KEY, created TEXT NOT NULL)")
            # Only articles that went through the existing scoring path may be delivered.
            try:
                scored = store.db.execute("SELECT DISTINCT article_id,topic_id FROM scores ORDER BY rowid DESC").fetchall()
            except Exception as error:
                import sqlite3
                if isinstance(error, sqlite3.OperationalError) and "no such table" in str(error):
                    return {"delivered": 0, "pending": 0}
                raise
        article_ids = list(dict.fromkeys(aid for aid, tid in scored if tid in topic_ids))[:limit]
        delivered, pending = 0, 0
        for article_id in article_ids:
            article = store.article(article_id)
            if not article.is_fresh(now):
                continue
            key = f"{self.base_url}|{self.basket_id}|{article_id}"
            with store.lock:
                if store.db.execute("SELECT 1 FROM app_deliveries WHERE key=?", (key,)).fetchone():
                    continue
            result = self._request("POST", self.path + "/articles", json=asdict(article))
            if result.get("skipped") or not result.get("saved"):
                pending += 1
                continue
            with store.lock, store.db:
                store.db.execute("INSERT OR IGNORE INTO app_deliveries VALUES (?,?)", (key, now.isoformat()))
            delivered += 1
        return {"delivered": delivered, "pending": pending}


def main(argv=None):
    from headline_model.paths import DATA_ROOT, MODEL_ROOT
    from news_agent.__main__ import cycle, load_env, parser as agent_parser
    from news_agent.core import Store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basket-id", type=int, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--state", type=Path, default=DATA_ROOT / "state" / "basket-news-agent.sqlite3")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--requests", type=int)
    parser.add_argument("--model-calls", type=int)
    parser.add_argument("--score-calls", type=int)
    parser.add_argument("--tool-calls", type=int)
    parser.add_argument("--interval", type=float, help="Repeat until interrupted; seconds between completed runs")
    args = parser.parse_args(argv)
    if args.interval is not None and args.interval < 10:
        parser.error("Choose an interval of at least 10 seconds")
    if args.discover and any(v is None or v < 1 for v in (args.requests, args.model_calls, args.score_calls, args.tool_calls)):
        parser.error("Discovery requires explicit positive request, model, score and tool budgets")
    load_env(MODEL_ROOT / ".env")
    bridge = AppBridge(args.api_url, os.environ.get("NEWS_APP_TOKEN", ""), args.basket_id)
    store = Store(args.state)
    try:
        while True:
            watchlist = bridge.watchlist()
            path = args.state.parent / f"basket-{args.basket_id}-watchlist.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(watchlist, indent=2) + "\n")
            result = {}
            if args.discover:
                command = agent_parser().parse_args(["--state", str(args.state), "run", "--watchlist", str(path),
                    "--requests", str(args.requests), "--model-calls", str(args.model_calls),
                    "--tool-calls", str(args.tool_calls), "--score", "--score-calls", str(args.score_calls)])
                result["agent"] = cycle(command, store)
            result["delivery"] = bridge.deliver(store, watchlist["topics"])
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.interval is None:
                break
            time.sleep(args.interval)
    finally:
        store.close()
        bridge.client.close()


if __name__ == "__main__":
    main()
