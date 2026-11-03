"""Reproducible Day 1 training: python -m headline_model --help."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys

from .paths import MODEL_ROOT, load_env
from .dataset import DEFAULT_DATASET, ROOT, load_dataset, pilot_rows, write_json
from .features import FeatureCache, extract_rows
from .training import fit_model, prepared_frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=MODEL_ROOT / '.env')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'reports/training')
    parser.add_argument('--cache', type=Path, default=ROOT / 'state/training/features.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('audit')
    sub.add_parser('status')
    replay = sub.add_parser('replay', help='Replay original dated training examples through the separate agent and model.')
    replay.add_argument('--limit', type=int, default=6)
    replay.add_argument('--model', type=Path)
    replay.add_argument('--without-agent', action='store_true')
    predict = sub.add_parser('predict', help='Score an article against one exact held contract.')
    predict.add_argument('--article', type=Path, required=True)
    predict.add_argument('--contract', type=Path, required=True)
    predict.add_argument('--model', type=Path)
    predict.add_argument('--historical', action='store_true', help='Explicit offline replay; retains original timestamps.')
    for command in ['pipeline', 'fit']:
        p = sub.add_parser(command)
        p.add_argument('--phase', choices=['pilot', 'full'], default='pilot')
        p.add_argument('--per-class', type=int, default=50)
        p.add_argument('--output', type=Path)
        if command == 'pipeline':
            p.add_argument('--workers', type=int, default=4)
            p.add_argument('--max-calls', type=int, required=True, help='Maximum uncached pairs; each pair has at most three HTTP attempts.')
    args = parser.parse_args(argv)
    load_env(args.env)
    if args.command == 'replay':
        try:
            from .replay import replay
            result = replay(limit=args.limit, model=args.model, use_agent=not args.without_agent)
            print(json.dumps(result, indent=2))
            return 0
        except (ValueError, OSError, KeyError, TypeError, ImportError):
            print(json.dumps({'error': 'Replay failed. Check the trained artifact, separate agent installation and Meta access.'}), file=sys.stderr)
            return 1
    if args.command == 'predict':
        from .predictor import Predictor
        predictor = None
        try:
            predictor = Predictor(args.model, max_calls=1)
            print(json.dumps(predictor.predict(json.loads(args.article.read_text()), json.loads(args.contract.read_text()), historical=args.historical), indent=2))
            return 0
        except (ValueError, OSError, KeyError, TypeError):
            print(json.dumps({'error': 'Prediction failed. Check the trained artifact, exact contract input and Meta access.'}), file=sys.stderr)
            return 1
        finally:
            if predictor:
                predictor.close()
    if args.command == 'status':
        result = {}
        for name in ['dataset-audit', 'pilot-extraction', 'pilot-model', 'full-extraction', 'full-model', 'pipeline-status']:
            p = args.run_dir / (name + '.json')
            if p.exists():
                r = json.loads(p.read_text())
                result[name] = {k: v for k, v in r.items() if k not in {'grouped_cv', 'dev', 'final', 'failures', 'dataset'}}
        print(json.dumps(result, indent=2))
        return 0
    args.run_dir.mkdir(parents=True, exist_ok=True)
    lock = (args.run_dir / 'pipeline.lock').open('a')
    cache = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rows, excluded, audit = load_dataset(args.dataset)
        write_json(args.run_dir / 'dataset-audit.json', audit)
        write_json(args.run_dir / 'excluded-rows.json', excluded)
        if args.command == 'audit':
            print(json.dumps(audit, indent=2))
            return 0
        selected = pilot_rows(rows, args.per_class) if args.phase == 'pilot' else rows
        write_json(args.run_dir / (args.phase + '-manifest.json'), {'dataset_sha256': audit['sha256'], 'pair_ids': [r['pair_id'] for r in selected], 'phase': args.phase})
        cache = FeatureCache(args.cache)
        model = os.environ.get('MUSE_MODEL') or 'muse-spark-1.3'
        if args.command == 'pipeline':
            write_json(args.run_dir / 'pipeline-status.json', {'status': 'EXTRACTING', 'phase': args.phase, 'pid': os.getpid(), 'started_at_utc': datetime.now(timezone.utc).isoformat()})
            extraction = extract_rows(selected, cache, workers=args.workers, max_calls=args.max_calls, progress=args.run_dir / (args.phase + '-extraction.json'))
            print(json.dumps({'extraction': extraction}), flush=True)
            if not extraction['processed_all']:
                raise ValueError('Extraction incomplete; successful rows are saved. Resume the same command after resolving provider failures.')
        frame = prepared_frame(selected, cache, model, allow_quarantined=args.phase=='full')
        frame.to_csv(args.run_dir / (args.phase + '-features.csv'), index=False)
        output = args.output or MODEL_ROOT / 'models' / ('ridge-v3-' + args.phase + '.joblib')
        report = fit_model(frame, dataset_report=audit, output=output, report_path=args.run_dir / (args.phase + '-model.json'), phase=args.phase)
        # Both artifacts are experimental. This pointer is used only by explicit
        # local sentiment previews and never enables exchange execution.
        write_json(MODEL_ROOT / 'models/active.json', {'artifact': str(output.resolve()), 'sha256': report['artifact_sha256'], 'experimental': True, 'phase': args.phase})
        write_json(args.run_dir / 'pipeline-status.json', {'status': 'COMPLETE_WITH_QUARANTINED_ROWS' if frame.attrs.get('quarantined') else 'COMPLETE', 'phase': args.phase, 'pid': os.getpid(), 'artifact': str(output.resolve()), 'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'training': {k: report[k] for k in ['artifact', 'train_rows', 'train_story_groups', 'alpha', 'fit_seconds', 'experimental']}}), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, ValueError) else 'Check local training files and settings.'
        if not isinstance(error, BlockingIOError):
            write_json(args.run_dir / 'pipeline-status.json', {'status': 'STOPPED', 'error': message, 'pid': os.getpid(), 'updated_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'error': message}), file=sys.stderr)
        return 1
    finally:
        if cache:
            cache.close()
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
