"""Run with python -m news_agent. All monitoring stays in the foreground."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from .core import AgentError, Store, load_topics, utc
from .providers import HTTP, collect, deliver
from headline_model.paths import DATA_ROOT

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path):
    if not path.is_file():
        return
    allowed = {'MODEL_API_KEY', 'MUSE_MODEL', 'NEWS_API_KEY', 'MUSE_UPDATES_TOKEN'}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key.strip() in allowed:
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def ridge_status():
    try:
        from headline_model.paths import MODEL_ROOT
    except ImportError:
        return {'connected': False, 'reason': 'Install the local slicefund-headline-engine package for scoring.'}
    pointer = MODEL_ROOT / 'models/active.json'
    return json.loads(pointer.read_text()) if pointer.exists() else {'connected': True, 'trained_artifact': False, 'model_project': str(MODEL_ROOT)}


def parser():
    result = argparse.ArgumentParser(description='LangChain/Muse news discovery and classifier queue handoff.')
    result.add_argument('--state', type=Path, default=DATA_ROOT / 'state' / 'agent.sqlite3')
    result.add_argument('--env', type=Path, default=ROOT / '.env')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    scoring = commands.add_parser('score', help='Preview Muse/Ridge sentiment for staged articles and exact held contracts.')
    scoring.add_argument('--watchlist', type=Path, required=True)
    scoring.add_argument('--model-calls', type=int, required=True)
    scoring.add_argument('--model', type=Path)
    scoring.add_argument('--limit', type=int, default=40)
    for name in ['collect', 'run', 'watch', 'deliver']:
        command = commands.add_parser(name)
        command.add_argument('--requests', type=int, required=True,
                             help='Explicit maximum HTTP attempts per run, including retries and delivery.')
        command.add_argument('--daily-requests', type=int,
                             help='Optional persistent daily cap per HTTP provider, UTC day.')
        if name in {'deliver', 'run', 'watch'}:
            command.add_argument('--intake-url', default='http://127.0.0.1:8765/v1/muse/updates')
        if name != 'deliver':
            command.add_argument('--watchlist', type=Path, required=True,
                                 help='Topic watchlist or existing portfolio JSON with exact position rules.')
            command.add_argument('--provider', choices=['rss', 'newsapi'], default='rss')
            command.add_argument('--feeds', type=Path, default=ROOT / 'news_feeds.json')
            command.add_argument('--cache-ttl', type=int, default=120)
            command.add_argument('--max-age', type=int, default=3600)
        if name in {'run', 'watch'}:
            command.add_argument('--model-calls', type=int, required=True)
            command.add_argument('--tool-calls', type=int, required=True)
            command.add_argument('--candidate-limit', type=int, default=40)
            command.add_argument('--deliver', action='store_true', help='Also deliver staged updates to the configured intake.')
            command.add_argument('--score', action='store_true', help='Also preview Ridge sentiment for exact contracts.')
            command.add_argument('--score-calls', type=int, default=4, help='Additional Muse evidence calls for Ridge scoring, separate from discovery calls.')
            command.add_argument('--model', type=Path)
        if name == 'watch':
            command.add_argument('--interval', type=float, required=True, help='Seconds between completed cycles.')
    return result


def cycle(args, store):
    if args.command == 'score':
        from .scoring import score_staged
        return score_staged(store, load_topics(args.watchlist), model_calls=args.model_calls,
                            artifact=args.model, limit=args.limit)
    http = HTTP(store, args.requests, args.daily_requests)
    if args.command == 'deliver':
        return deliver(store, http, args.intake_url, os.environ.get('MUSE_UPDATES_TOKEN', ''))
    if args.command in {'run', 'watch'} and not os.environ.get('MODEL_API_KEY', '').strip():
        raise AgentError('Set a replacement MODEL_API_KEY locally; collect works without a model key.')
    if args.command in {'run', 'watch'} and args.deliver and not os.environ.get('MUSE_UPDATES_TOKEN', '').strip():
        raise AgentError('Configure the separate MUSE_UPDATES_TOKEN before choosing delivery.')
    topics = load_topics(args.watchlist)
    if args.command in {'run', 'watch'} and args.score:
        if args.score_calls < 1 or not any(t.get('contract') for t in topics):
            raise AgentError('Choose a positive scoring budget and a watchlist with exact held contracts before running --score.')
        if args.model is None:
            from headline_model.paths import MODEL_ROOT
            if not (MODEL_ROOT / 'models/active.json').exists():
                raise AgentError('Train the Ridge pilot before running --score, or select an existing --model artifact.')
    now = datetime.now(timezone.utc)
    topics = [topic for topic in topics if not topic.get('contract') or utc(topic['contract']['deadline']) > now]
    if not topics:
        return {'candidates': [], 'requests_used': 0, 'note': 'All supplied contracts are past their resolution deadlines.'}
    feeds = json.loads(args.feeds.read_text())['feeds'] if args.provider == 'rss' else None
    result = collect(store, http, topics, args.provider, feeds=feeds,
                     news_key=os.environ.get('NEWS_API_KEY', ''), ttl=args.cache_ttl,
                     max_age=args.max_age)
    if args.command in {'run', 'watch'}:
        from .agent import run_agent
        result['agent'] = run_agent(store, topics, result['candidates'],
                                    model_budget=args.model_calls, tool_budget=args.tool_calls,
                                    candidate_limit=args.candidate_limit)
        if args.deliver:
            result['delivery'] = deliver(store, http, args.intake_url,
                                         os.environ.get('MUSE_UPDATES_TOKEN', ''))
        if args.score:
            from .scoring import score_staged
            result['scoring'] = score_staged(store, topics, model_calls=args.score_calls,
                                            artifact=args.model, limit=args.candidate_limit)
    result['requests_used'] = http.used
    result['execution'] = 'NO_EXCHANGE_ORDER_CAPABILITY'
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        load_env(args.env)
        if args.command == 'watch' and (not 0 < args.interval < 86400):
            raise AgentError('Choose a positive monitoring interval below one day.')
        if args.command in {'run', 'watch'} and min(args.model_calls, args.tool_calls, args.candidate_limit) < 1:
            raise AgentError('Model/tool budgets and candidate limit must be positive.')
        store = Store(args.state)
        if args.command == 'status':
            print(json.dumps({**store.status(), 'model_key_configured': bool(os.environ.get('MODEL_API_KEY', '').strip()),
                              'newsapi_key_configured': bool(os.environ.get('NEWS_API_KEY', '').strip()),
                              'intake_token_configured': bool(os.environ.get('MUSE_UPDATES_TOKEN', '').strip()),
                              'muse_model': os.environ.get('MUSE_MODEL') or 'muse-spark-1.3',
                              'news_provider': 'RSS needs no provider key; NewsAPI needs its own key',
                              'news_discovery': 'Only while collect/run/watch is explicitly running',
                              'ridge_model': ridge_status(),
                              'exchange_execution': 'NOT_CONNECTED'}))
            return 0
        while True:
            result = cycle(args, store)
            result['completed_at'] = datetime.now(timezone.utc).isoformat()
            print(json.dumps(result), flush=True)
            has_errors = bool(result.get('errors') or result.get('delivery', {}).get('errors'))
            if args.command != 'watch':
                return 2 if has_errors else 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(json.dumps({'monitoring': 'stopped'}))
        return 0
    except (AgentError, OSError, ValueError, KeyError, TypeError):
        # Handle expected errors without serializing a URL/header or SDK traceback.
        import sys
        error = sys.exception()
        message = str(error) if isinstance(error, AgentError) else 'Check local input files, settings, and permissions.'
        print(json.dumps({'error': message, 'exchange_execution': 'NOT_CONNECTED'}))
        return 1
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
