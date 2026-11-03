"""Local, reviewable headline feature. No live exchange orders are sent."""
import argparse
import csv
import hashlib
import hmac
from datetime import datetime, timezone
from decimal import InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from .engine import HeadlineEngine, Journal, Position, utc
from .muse_matcher import MetaClient, MuseMatcher
from .ridge_model import FEATURES, PositionRidge, normalize

ROOT = Path(__file__).resolve().parents[1]
REVIEW_FIELDS = ['headline_id', 'position_id', 'evidence_model', 'prompt_version', *FEATURES, 'sentiment', 'related', 'confidence', 'headline', 'market_question', 'held_outcome', 'reason', 'evidence']


def load_env(path=ROOT / '.env'):
    """Read only this feature's settings as literal strings, without running shell code."""
    if path.is_file():
        allowed = {'MODEL_API_KEY', 'MUSE_MODEL', 'RIDGE_MODEL_PATH', 'REDUCE_FRACTION', 'MUSE_UPDATES_TOKEN'}
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in allowed:
                    os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def model_path():
    configured = os.environ.get('RIDGE_MODEL_PATH')
    if configured:
        return Path(configured)
    active = ROOT / 'models/active.json'
    if active.is_file():
        from .paths import active_model
        return active_model()[0]
    return ROOT / 'models/ridge.joblib'


def status():
    return {
        'feature': 'Muse relevance + Ridge held-position sentiment',
        'muse_model': os.environ.get('MUSE_MODEL') or 'muse-spark-1.3',
        'api_key_configured': bool(os.environ.get('MODEL_API_KEY', '').strip()),
        'ridge_artifact_present': model_path().is_file(),
        'automatic_dataset_loading': False,
        'update_source': 'Muse app/agent',
        'update_ingest_token_configured': bool(os.environ.get('MUSE_UPDATES_TOKEN', '').strip()),
        'exchange_execution': 'NOT_CONNECTED',
        'repository_integration': 'AWAITING_FEATURE_APPROVAL',
    }


def build_engine(args, journal):
    fraction = getattr(args, 'reduce_fraction', None) or os.environ.get('REDUCE_FRACTION')
    if not fraction:
        raise ValueError('Set --reduce-fraction explicitly: 0.5 means sell half, for example. No reduction amount is chosen for you.')
    path = getattr(args, 'model', None) or model_path()
    scorer = PositionRidge(path) if Path(path).is_file() else None
    matcher = MuseMatcher(MetaClient()) if os.environ.get('MODEL_API_KEY', '').strip() else None
    return HeadlineEngine(scorer, journal, fraction, matcher=matcher, negative_threshold=getattr(args, 'negative_threshold', -0.5))


def read_json(path):
    return json.loads(Path(path).read_text())


def emit(value):
    print(json.dumps(value), flush=True)


def scan_file(args, engine):
    return engine.process(read_json(args.article), read_json(args.portfolio))


def consume_queue(args, engine, journal):
    """Consume complete JSONL records; retry partial writes and stale portfolio reads."""
    path = args.headlines.resolve()
    stat = path.stat()
    signature = f'{stat.st_dev}:{stat.st_ino}'
    offset = journal.offset(path, signature)
    if offset > stat.st_size:
        offset = 0
    portfolio = read_json(args.portfolio)
    age = (datetime.now(timezone.utc)-utc(portfolio['as_of'])).total_seconds()
    if not 0 <= age <= 60:
        raise ValueError('Refresh the portfolio snapshot; queued headlines have not been consumed.')
    processed = 0
    with path.open('rb') as feed:
        feed.seek(offset)
        while True:
            line = feed.readline()
            if not line or not line.endswith(b'\n'):
                break
            # Re-read per headline so the backend can refresh positions between events.
            portfolio = read_json(args.portfolio)
            age = (datetime.now(timezone.utc)-utc(portfolio['as_of'])).total_seconds()
            if not 0 <= age <= 60:
                raise ValueError('Refresh portfolio snapshot; remaining headlines are still queued.')
            try:
                if len(line) > 100_000:
                    raise ValueError('Headline queue record exceeds 100 KB.')
                article = json.loads(line)
                decisions = engine.process(article, portfolio)
                for decision in decisions:
                    emit(decision)
            except (ValueError, KeyError, TypeError, InvalidOperation) as error:
                emit({'action': 'HOLD', 'reason': 'invalid_queue_record', 'detail': str(error), 'byte_offset': offset})
            offset = feed.tell()
            journal.save_offset(path, signature, offset)
            processed += 1
    return processed


def review_record(article, position, assessment, sentiment=''):
    """Use one evidence export schema for portfolio and training-pair inputs."""
    return {
        'headline_id': hashlib.sha256(normalize(article['headline']).encode()).hexdigest(),
        'position_id': position.id, 'evidence_model': assessment.model,
        'prompt_version': assessment.prompt_version, **assessment.features,
        'sentiment': sentiment, 'related': assessment.related,
        'confidence': assessment.confidence, 'headline': article['headline'],
        'market_question': position.question, 'held_outcome': position.outcome,
        'reason': assessment.reason, 'evidence': json.dumps(assessment.evidence),
    }


def extract(args):
    """Create a review file with blank HUMAN labels; never train or fill labels."""
    if args.output.exists():
        raise ValueError('Review output already exists; choose a new filename.')
    article, portfolio = read_json(args.article), read_json(args.portfolio)
    positions = [Position.parse(item) for item in portfolio['positions']]
    matcher = MuseMatcher(MetaClient())
    candidates = matcher.candidates(article, positions)
    records = []
    for position in positions:
        if position.id not in candidates:
            continue
        assessment = matcher.assess(article, position)
        records.append(review_record(article, position, assessment))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if records:
        with args.output.open('w', newline='') as target:
            writer = csv.DictWriter(target, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(records)
    return {'review_rows': len(records), 'output': str(args.output), 'labels': 'BLANK', 'trained': False}


def prepare(args):
    """Batch evidence extraction for a CSV of human-reviewed headline/contract pairs."""
    if args.output.exists():
        raise ValueError('Output already exists; choose a new filename.')
    with args.csv.open(newline='') as source:
        reader = csv.DictReader(source)
        fields = {'headline', 'published_at', 'position_id', 'market_question', 'position_thesis', 'outcome', 'resolution_rules', 'deadline', 'sentiment'}
        if fields - set(reader.fieldnames or []):
            raise ValueError('Use examples/labeled_headlines.template.csv for the raw training-pair columns.')
        rows = list(reader)
    if not rows:
        raise ValueError('The raw training template has no rows yet.')
    # Validate all rows before making any paid provider calls.
    for row in rows:
        if any(not row.get(field, '').strip() for field in fields - {'sentiment'}):
            raise ValueError('Raw pair metadata cannot be blank.')
        if row['outcome'] not in {'YES', 'NO'} or row['sentiment'] not in {'', '-1', '0', '1'}:
            raise ValueError('Outcome must be YES/NO and human sentiment blank or -1/0/1.')
        utc(row['published_at'])
        utc(row['deadline'])
        if len(row['headline']) > 2000 or len(row.get('summary', '')) > 12000:
            raise ValueError('Headline or summary exceeds supported input length.')
    client = MetaClient()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as destination:
        writer = csv.DictWriter(destination, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            p = SimpleNamespace(id=row['position_id'], question=row['market_question'], thesis=row['position_thesis'], outcome=row['outcome'], resolution_rules=row['resolution_rules'], deadline=row['deadline'])
            news = {'headline': row['headline'], 'summary': row.get('summary', ''), 'published_at': row['published_at']}
            assessment = client.assess(news, p)
            writer.writerow(review_record(news, p, assessment, row['sentiment']))
            destination.flush()  # Preserve completed review rows if a later API call fails.
    return {'review_rows': len(rows), 'output': str(args.output), 'trained': False}


def handler_for(args):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, status()) if self.path == '/health' else self.reply(404, {'error': 'unknown_route'})

        def do_POST(self):
            if self.path == '/v1/muse/updates':
                token = os.environ.get('MUSE_UPDATES_TOKEN', '')
                if not token.strip():
                    self.reply(503, {'error': 'Muse update ingestion is disabled until MUSE_UPDATES_TOKEN is configured.'})
                    return
                supplied = self.headers.get('Authorization', '')
                if not hmac.compare_digest(supplied.encode(), ('Bearer '+token).encode()):
                    self.reply(401, {'error': 'Unauthorized'})
                    return
                queue = None
                try:
                    from .update_receiver import UpdateQueue, validate_update
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 100_000:
                        raise ValueError('Supply a JSON update no larger than 100 KB.')
                    payload = json.loads(self.rfile.read(length))
                    article = validate_update(payload['article'])
                    queue = UpdateQueue(args.updates_state)
                    appended = queue.append([article], args.updates)
                    self.reply(202, {'accepted': True, 'queued': bool(appended), 'duplicate': not bool(appended), 'trained': False, 'scored': False, 'execution': 'NOT_SUBMITTED'})
                except (ValueError, KeyError, TypeError) as error:
                    self.reply(400, {'error': str(error), 'execution': 'NOT_SUBMITTED'})
                finally:
                    if queue:
                        queue.close()
                return
            if self.path != '/v1/headlines/assess':
                self.reply(404, {'error': 'unknown_route'})
                return
            journal = None
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 1_000_000:
                    raise ValueError('Supply a JSON request no larger than 1 MB.')
                payload = json.loads(self.rfile.read(length))
                journal = Journal(args.journal)
                engine = build_engine(args, journal)
                self.reply(200, {'decisions': engine.process(payload['article'], payload['portfolio']), 'execution': 'NOT_SUBMITTED'})
            except (ValueError, KeyError, TypeError, InvalidOperation) as error:
                self.reply(400, {'error': str(error), 'execution': 'NOT_SUBMITTED'})
            finally:
                if journal:
                    journal.close()

        def log_message(self, format, *values):
            pass  # Do not log request bodies, headers or account information.
    return Handler


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('status')
    train = sub.add_parser('train', help='Train only after reviewing your own labeled CSV')
    train.add_argument('--csv', type=Path, required=True)
    train.add_argument('--output', type=Path, default=model_path())
    extraction = sub.add_parser('extract', help='Muse evidence for review, leaving human labels blank')
    extraction.add_argument('--article', type=Path, required=True)
    extraction.add_argument('--portfolio', type=Path, required=True)
    extraction.add_argument('--output', type=Path, required=True)
    preparation = sub.add_parser('prepare', help='Convert raw headline/contract pairs to reviewable Muse features; does not train')
    preparation.add_argument('--csv', type=Path, required=True)
    preparation.add_argument('--output', type=Path, required=True)
    for name in ['scan', 'watch', 'serve']:
        command = sub.add_parser(name)
        command.add_argument('--model', type=Path, default=model_path())
        command.add_argument('--reduce-fraction', help='Explicit fraction of the current position to sell')
        command.add_argument('--negative-threshold', type=float, default=-0.5)
        command.add_argument('--journal', type=Path, default=ROOT / 'state' / 'decisions.sqlite')
        if name == 'serve':
            command.add_argument('--port', type=int, default=8765)
            command.add_argument('--updates', type=Path, default=ROOT / 'state' / 'muse_updates.jsonl')
            command.add_argument('--updates-state', type=Path, default=ROOT / 'state' / 'muse_ingest.sqlite')
        else:
            command.add_argument('--portfolio', type=Path, required=True)
            if name == 'scan':
                command.add_argument('--article', type=Path, required=True)
            else:
                command.add_argument('--headlines', type=Path, required=True)
                command.add_argument('--interval', type=float, default=5)
                command.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    journal = None
    try:
        if args.command in {None, 'status'}:
            emit(status())
        elif args.command == 'train':
            if args.output.exists():
                raise ValueError('Model output already exists. Choose a new artifact path.')
            emit(PositionRidge().train(args.csv, args.output))
        elif args.command == 'extract':
            emit(extract(args))
        elif args.command == 'prepare':
            emit(prepare(args))
        elif args.command == 'serve':
            # Loopback only. The existing authenticated backend can proxy after approval.
            server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args))
            emit({'listening': f'http://127.0.0.1:{server.server_port}', 'exchange_execution': 'NOT_CONNECTED'})
            try:
                server.serve_forever()
            finally:
                server.server_close()
        else:
            journal = Journal(args.journal)
            engine = build_engine(args, journal)
            if args.command == 'scan':
                emit({'decisions': scan_file(args, engine), 'execution': 'NOT_SUBMITTED'})
            else:
                if args.interval <= 0 or args.interval > 60:
                    raise ValueError('Polling interval must be between 0 and 60 seconds.')
                if engine.scorer is None or engine.matcher is None:
                    raise ValueError('Watch mode needs a trained Ridge artifact and the Muse key. No background process started.')
                while True:
                    try:
                        consume_queue(args, engine, journal)
                    except (ValueError, KeyError, FileNotFoundError) as error:
                        emit({'worker': 'waiting', 'reason': str(error)})
                    if args.once:
                        break
                    time.sleep(args.interval)
        return 0
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation) as error:
        emit({'error': str(error), 'exchange_execution': 'NOT_SUBMITTED'})
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if journal:
            journal.close()


if __name__ == '__main__':
    sys.exit(main())
