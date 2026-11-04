"""App-owned news discovery; no broker or exchange capability.

Only short snapshot/delivery transactions touch the application database. Provider
requests run outside those transactions and outside the market-sync lock.
"""
import json
import logging
import threading
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from urllib.request import build_opener

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.models import Basket, as_utc, utcnow
from app.news_models import NewsMonitorStatus, NewsMonitorUsage
from app.services import news

log = logging.getLogger(__name__)


def _redacted_failure():
    """Exception class chain and code locations, without messages (which may hold keys)."""
    import sys
    import traceback
    error, chain = sys.exc_info()[1], []
    while error is not None and len(chain) < 5:
        chain.append(f"{type(error).__module__}.{type(error).__qualname__}")
        error = error.__cause__ or error.__context__
    frames = [f"{Path(f.filename).name}:{f.lineno} {f.name}"
              for f in traceback.extract_tb(sys.exc_info()[2])[-6:]]
    return " <- ".join(chain) + " | " + " > ".join(frames)


class MonitorStopped(ValueError):
    pass


class BudgetReached(ValueError):
    pass


class InstanceLock:
    """One collector per app database, across API processes (and PostgreSQL hosts)."""

    def __init__(self, engine, state_path):
        self.engine, self.state_path = engine, state_path
        self.handle = None

    def acquire(self):
        if self.engine.dialect.name == "postgresql":
            connection = self.engine.connect()
            try:
                acquired = connection.scalar(text("SELECT pg_try_advisory_lock(73628491052631)"))
                connection.commit()
                if acquired:
                    self.handle = connection
                    return True
            except Exception:
                connection.close()
                raise
            connection.close()
            return False
        import fcntl
        database = self.engine.url.database
        base = Path(database) if database not in (None, "", ":memory:") else self.state_path
        path = Path(str(base.resolve()) + ".news-monitor.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle is None:
            return
        try:
            if self.engine.dialect.name == "postgresql":
                try:
                    self.handle.execute(text("SELECT pg_advisory_unlock(73628491052631)"))
                    self.handle.commit()
                except SQLAlchemyError:
                    self.handle.invalidate()
        finally:
            self.handle.close()
            self.handle = None


class NewsMonitor:
    def __init__(self, settings, sessionmaker, engine, *, model_factory=None,
                 source_opener=None, evidence_opener=None, discovery_transport=None):
        self.settings, self.sessions, self.engine = settings, sessionmaker, engine
        self.stop = threading.Event()
        self.thread = None
        self.model_factory = model_factory
        self.source_opener, self.evidence_opener = source_opener, evidence_opener
        self.discovery_transport = discovery_transport
        self.local_status = "STARTING"
        self.checked_at = None

    def start(self):
        if not self.settings.news_monitor_enabled:
            self.local_status = "DISABLED"
        elif not self.settings.meta_api_key:
            self.local_status = "WAITING_FOR_KEY"
        else:
            self.thread = threading.Thread(target=self._run, name="basket-news-monitor", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=50)
            if self.thread.is_alive():
                log.warning("News monitor is finishing an in-flight request; no further requests will start")

    def _check_stop(self):
        if self.stop.is_set():
            raise MonitorStopped("News monitor stopped")

    def _sleep(self, seconds):
        if self.stop.wait(seconds):
            raise MonitorStopped("News monitor stopped")

    def _status(self, status, note, *, checked=False):
        self.local_status = status
        if checked:
            self.checked_at = utcnow()
        with self.sessions() as session:
            item = session.get(NewsMonitorStatus, 1)
            if item is None:
                item = NewsMonitorStatus(id=1, checked_at=None)
                session.add(item)
            item.status, item.note, item.updated_at = status, note, utcnow()
            if self.checked_at:
                item.checked_at = self.checked_at
            session.commit()

    def _initialize(self):
        with self.sessions() as session:
            for kind in ("discovery", "evidence"):
                if session.get(NewsMonitorUsage, kind) is None:
                    session.add(NewsMonitorUsage(kind=kind, attempts=0))
            saved = session.get(NewsMonitorStatus, 1)
            paused = saved is not None and saved.status == "PAUSED_ERROR"
            session.commit()
        return not paused or self.settings.news_monitor_retry_errors

    def _remaining(self, kind):
        limit = getattr(self.settings, f"news_monitor_{kind}_limit")
        with self.sessions() as session:
            item = session.get(NewsMonitorUsage, kind)
            return max(0, limit - (item.attempts if item else 0))

    def _reserve(self, kind):
        self._check_stop()
        limit = getattr(self.settings, f"news_monitor_{kind}_limit")
        with self.sessions() as session:
            reserved = session.execute(update(NewsMonitorUsage).where(
                NewsMonitorUsage.kind == kind, NewsMonitorUsage.attempts < limit
            ).values(attempts=NewsMonitorUsage.attempts + 1))
            if reserved.rowcount != 1:
                session.rollback()
                raise BudgetReached("News request allowance reached")
            session.commit()  # A crash after reservation still counts the attempt.

    def _open(self, opener, request, timeout):
        self._check_stop()
        with self.sessions() as session:
            session.execute(update(NewsMonitorStatus).where(NewsMonitorStatus.id == 1)
                            .values(updated_at=utcnow()))
            session.commit()
        return opener(request, timeout=min(timeout, 45))

    def _evidence_open(self, opener, request, timeout):
        self._reserve("evidence")  # Every MetaClient retry passes through here.
        return self._open(opener, request, timeout)

    def _discovery_request(self, request):
        self._reserve("discovery")  # Runs before httpx sends each model request.

    def _watchlists(self):
        result = {}
        with self.sessions() as session:
            for basket in session.scalars(select(Basket).order_by(Basket.id)):
                snapshot = news.watchlist(session, basket)
                open_ids = {p.market_id for p in basket.positions if p.market.is_open}
                topics = [t for t in snapshot["topics"] if t["market_id"] in open_ids
                          and news.utc(t["contract"]["deadline"]) > utcnow()]
                if topics:
                    result[basket.id] = topics
        return result

    def _deliver(self, store, watchlists):
        """Revalidate cached evidence and current owner/constituents before ingestion."""
        with store.lock:
            rows = store.db.execute("SELECT DISTINCT article_id,topic_id FROM scores ORDER BY rowid DESC").fetchall()
        for basket_id, topics in watchlists.items():
            topic_ids = {t["id"] for t in topics}
            article_ids = list(dict.fromkeys(a for a, t in rows if t in topic_ids))[:40]
            if not article_ids:
                continue
            # Cache-only scorer has no paid transport or trading capability.
            predictor = news.make_predictor(self.settings)
            try:
                for article_id in article_ids:
                    self._check_stop()
                    article = store.article(article_id)
                    if not article.is_fresh(utcnow()):
                        continue
                    with self.sessions() as session:
                        basket = session.get(Basket, basket_id)
                        if basket is None:
                            continue
                        news.ingest(session, basket, article.__dict__, predictor)
                        session.commit()
            finally:
                predictor.close()

    def cycle(self, store):
        import httpx
        from headline_model.muse_matcher import MetaClient
        from headline_model.paths import MODEL_ROOT
        from headline_model.predictor import Predictor
        from news_agent.agent import make_model, run_agent
        from news_agent.providers import HTTP, NoRedirect, collect
        from news_agent.scoring import score_staged

        self._check_stop()
        watchlists = self._watchlists()
        topics = [t for group in watchlists.values() for t in group]
        if not topics:
            self._status("WAITING_FOR_BASKETS", "Waiting for saved ETFs with open contracts and exact exchange rules.")
            return
        # Keep the trained evidence model explicit; never mutate process-wide env.
        with ExitStack() as stack:
            evidence_opener = self.evidence_opener or build_opener(NoRedirect()).open
            client = MetaClient(key=self.settings.meta_api_key, model="muse-spark-1.3",
                                opener=lambda request, timeout: self._evidence_open(evidence_opener, request, timeout),
                                sleeper=self._sleep)
            predictor = Predictor(self.settings.news_model_path,
                                  cache_path=self.settings.news_feature_cache, max_calls=3, client=client)
            stack.callback(predictor.close)
            http_client = stack.enter_context(httpx.Client(
                transport=self.discovery_transport, follow_redirects=False, timeout=45,
                event_hooks={"request": [self._discovery_request]}))
            source_opener = self.source_opener or build_opener(NoRedirect()).open
            http = HTTP(store, 20, opener=lambda request, timeout: self._open(source_opener, request, timeout),
                        sleeper=self._sleep)
            feeds = json.loads((MODEL_ROOT / "news_feeds.json").read_text())["feeds"]
            collected = collect(store, http, topics, "rss", feeds=feeds)
            self._check_stop()
            context = news.digest(topics)
            with store.lock, store.db:
                store.db.execute("CREATE TABLE IF NOT EXISTS monitor_considered (key TEXT PRIMARY KEY)")
                considered = {r[0] for r in store.db.execute("SELECT key FROM monitor_considered")}
            candidates = [c for c in collected["candidates"]
                          if f"{context}:{c['article_id']}" not in considered][:20]
            remaining = self._remaining("discovery")
            if candidates and remaining:
                factory = self.model_factory or make_model
                model = factory(http_client=http_client, api_key=self.settings.meta_api_key,
                                model_name=self.settings.llama_model, timeout=45)
                run_agent(store, topics, candidates, model_budget=min(2, remaining),
                          tool_budget=20, candidate_limit=20, model=model)
                with store.lock, store.db:
                    store.db.executemany("INSERT OR IGNORE INTO monitor_considered VALUES (?)",
                                         [(f"{context}:{c['article_id']}",) for c in candidates])
            self._check_stop()
            try:
                score_staged(store, topics, model_calls=3, predictor=predictor)
            except BudgetReached:
                # Already validated results are preserved and may still be delivered.
                pass
            self._deliver(store, watchlists)
            exhausted = not self._remaining("discovery") or not self._remaining("evidence")
            if exhausted:
                self._status("BUDGET_EXHAUSTED", "Monitoring stopped at the approved request allowance.", checked=True)
            elif collected["errors"]:
                self._status("DEGRADED", "Some news feeds could not be read; available feeds were checked.", checked=True)
            else:
                self._status("MONITORING", "Checking news for saved ETF positions.", checked=True)

    def _run(self):
        lock = InstanceLock(self.engine, Path(self.settings.news_monitor_state))
        acquired = False
        try:
            while not self.stop.is_set() and not acquired:
                acquired = lock.acquire()
                if not acquired:
                    self.local_status = "STANDBY"
                    self.stop.wait(10)
            if not acquired or self.stop.is_set():
                return
            if not self._initialize():
                self.local_status = "PAUSED_ERROR"
                return
            from news_agent.core import Store
            store = Store(Path(self.settings.news_monitor_state))
            try:
                while not self.stop.is_set():
                    if not self._remaining("discovery") or not self._remaining("evidence"):
                        self._status("BUDGET_EXHAUSTED", "Monitoring stopped at the approved request allowance.")
                        break
                    self._status("CHECKING", "Checking news for saved ETF positions.")
                    self.cycle(store)
                    if self.local_status == "BUDGET_EXHAUSTED":
                        break
                    self.stop.wait(self.settings.news_monitor_interval_seconds)
            finally:
                store.close()
        except MonitorStopped:
            pass
        except Exception:  # noqa: BLE001 -- isolate this optional worker and redact provider data
            # Never log provider exceptions: their request objects can contain keys.
            log.warning("News monitoring paused after a failure; core app services remain available")
            log.warning("News monitor failure (types and frames only): %s", _redacted_failure())
            try:
                if not self._remaining("discovery") or not self._remaining("evidence"):
                    self._status("BUDGET_EXHAUSTED", "Monitoring stopped at the approved request allowance.")
                else:
                    self._status("PAUSED_ERROR", "News monitoring paused after a source, model, or evidence error. Check server configuration before resuming.")
            except SQLAlchemyError:
                self.local_status = "PAUSED_ERROR"
        finally:
            if acquired and self.stop.is_set():
                try:
                    self._status("STOPPED", "News monitoring stopped with the app.")
                except SQLAlchemyError:
                    log.warning("Could not save news monitor shutdown status")
            lock.close()

    def status(self, session):
        limits = {kind: getattr(self.settings, f"news_monitor_{kind}_limit") for kind in ("discovery", "evidence")}
        usage = {kind: 0 for kind in limits}
        usage.update({r.kind: r.attempts for r in session.scalars(select(NewsMonitorUsage))})
        saved = session.get(NewsMonitorStatus, 1)
        status = saved.status if saved else self.local_status
        note = saved.note if saved else "News monitoring is starting."
        if self.local_status == "DISABLED":
            status, note = "DISABLED", "Background news monitoring is disabled."
        elif self.local_status == "WAITING_FOR_KEY":
            status, note = "WAITING_FOR_KEY", "Configure the server's Meta API key to start news monitoring."
        elif saved and status in ("CHECKING", "MONITORING", "DEGRADED", "WAITING_FOR_BASKETS"):
            age = utcnow() - as_utc(saved.updated_at)
            if age > timedelta(seconds=self.settings.news_monitor_interval_seconds + 180):
                status, note = "UNAVAILABLE", "The news worker has not reported recently. Check the server."
        return {"status": status, "note": note,
                "checked_at": as_utc(saved.checked_at).isoformat() if saved and saved.checked_at else None,
                "interval_seconds": self.settings.news_monitor_interval_seconds,
                "requests": {kind: {"used": usage[kind], "limit": limits[kind]} for kind in limits}}
