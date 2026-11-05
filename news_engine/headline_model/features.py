"""Bounded, concurrent Muse extraction with content-addressed durable caching."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

from .dataset import digest, model_input, write_json
from .muse_matcher import INSTRUCTIONS, SCHEMA, MetaClient, validate_assessment
from .ridge_model import PROMPT_VERSION

EXTRACTOR_HASH = digest({'instructions': INSTRUCTIONS, 'schema': SCHEMA, 'prompt_version': PROMPT_VERSION})


def key_for(row, model):
    return digest({'input': model_input(row), 'model': model, 'extractor': EXTRACTOR_HASH})


def cached_assessment(data, row, model):
    """Apply the same provenance and evidence checks to every cache backend."""
    if data['model'] != model or data['prompt_version'] != PROMPT_VERSION:
        raise ValueError('Cached feature provenance mismatch.')
    values = {**data['features'], **{k: data[k] for k in ['related', 'confidence', 'reason', 'evidence']}}
    return validate_assessment(values, {'headline': row['headline'], 'summary': row['summary']}, model)


class FeatureCache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS features (key TEXT PRIMARY KEY, assessment TEXT NOT NULL, created TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS quarantined (key TEXT PRIMARY KEY, error TEXT NOT NULL, created TEXT NOT NULL)')
        self.db.commit()
        self.lock = threading.Lock()

    def get(self, row, model):
        with self.lock:
            result = self.db.execute('SELECT assessment FROM features WHERE key=?', (key_for(row, model),)).fetchone()
        if not result:
            return None
        return cached_assessment(json.loads(result[0]), row, model)

    def put(self, row, assessment):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO features VALUES (?,?,?)', (key_for(row, assessment.model), json.dumps(asdict(assessment)), datetime.now(timezone.utc).isoformat()))
            self.db.execute('DELETE FROM quarantined WHERE key=?', (key_for(row, assessment.model),))

    def failure(self, row, model):
        with self.lock:
            result = self.db.execute('SELECT error FROM quarantined WHERE key=?', (key_for(row, model),)).fetchone()
        return result[0] if result else None

    def quarantine(self, row, model, error):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO quarantined VALUES (?,?,?)', (key_for(row, model), error, datetime.now(timezone.utc).isoformat()))

    def close(self):
        self.db.close()


def extract_rows(rows, cache, *, workers=4, max_calls=10000, progress=None, client_factory=MetaClient):
    if workers not in range(1, 9) or max_calls < 1:
        raise ValueError('Choose 1–8 workers and a positive API call budget.')
    client = client_factory()
    model = client.model
    cached = [r for r in rows if cache.get(r, model) is not None]
    quarantined = [{'pair_id':r['pair_id'], 'split':r['split'], 'error':cache.failure(r,model)} for r in rows if cache.failure(r,model) and cache.get(r,model) is None]
    missing = [r for r in rows if cache.get(r, model) is None and not cache.failure(r, model)]
    if len(missing) > max_calls:
        raise ValueError('Uncached pairs exceed max-calls. Increase the explicit budget or use a smaller selection.')
    started = time.monotonic()
    report = {'target_rows': len(rows), 'cached_at_start': len(cached), 'completed_this_run': 0, 'failures': [], 'quarantined':quarantined, 'model': model, 'extractor_hash': EXTRACTOR_HASH, 'workers': workers, 'max_pair_calls': max_calls, 'max_http_attempts_per_pair': 3}

    def save():
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['cached_total'] = report['cached_at_start'] + report['completed_this_run']
        report['quarantined_total'] = len(report['quarantined'])
        report['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        if report['completed_this_run']:
            report['estimated_seconds_remaining'] = round(report['elapsed_seconds'] / report['completed_this_run'] * (len(missing) - report['completed_this_run'] - len(report['failures'])))
        if progress:
            write_json(progress, report)

    def work(row):
        c = client_factory()
        position = SimpleNamespace(question=row['market_question'], thesis=row['position_thesis'], outcome=row['outcome'], resolution_rules=row['resolution_rules'], deadline=row['deadline'])
        assessment = c.assess({'headline': row['headline'], 'summary': row['summary'], 'published_at': row['published_at']}, position)
        cache.put(row, assessment)
        return getattr(c, 'usage', [])

    save()
    # Submit only a small window. A permanent authentication or quota failure stops
    # new calls instead of burning the entire batch on a known provider failure.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}
        source = iter(missing)
        fatal = False
        consecutive_provider_errors = 0
        for _ in range(workers):
            row = next(source, None)
            if row is not None:
                pending[executor.submit(work, row)] = row
        while pending:
            future = next(as_completed(pending))
            row = pending.pop(future)
            try:
                usage = future.result()
                report['usage_this_run'] = {key: report.get('usage_this_run', {}).get(key, 0) + sum(u.get(key, 0) for u in usage) for key in ['input_tokens', 'output_tokens', 'total_tokens']}
                report['completed_this_run'] += 1
                consecutive_provider_errors = 0
            except Exception as error:
                # Deliberately avoid serializing provider request/response bodies.
                safe = str(error) if isinstance(error, ValueError) and str(error).startswith(('Meta API', 'Muse response', 'Invalid Muse')) else type(error).__name__
                report['failures'].append({'pair_id': row['pair_id'], 'error': safe})
                if safe.startswith(('Invalid Muse', 'Muse response')):
                    # No feature is invented. Invalid/refused outputs after the
                    # client's three attempts are excluded with durable reasons.
                    cache.quarantine(row, model, safe)
                    report['quarantined'].append({'pair_id':row['pair_id'], 'split':row['split'], 'error':safe})
                else:
                    consecutive_provider_errors += 1
                    fatal = fatal or any(code in safe for code in ['401', '402', '403', '429']) or consecutive_provider_errors >= 3 or not safe.startswith('Meta API')
                fatal = fatal or len(report['quarantined']) > max(8, int(.05 * len(rows)))
            save()
            row = None if fatal else next(source, None)
            if row is not None:
                pending[executor.submit(work, row)] = row
    report['complete'] = report['cached_total'] == len(rows)
    report['processed_all'] = report['cached_total'] + len(report['quarantined']) == len(rows) and not fatal
    report['stopped_for_provider_errors'] = fatal
    save()
    return report
