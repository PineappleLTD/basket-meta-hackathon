"""Fit only the frozen training partition; report held-out proposal agreement."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import classification_report, confusion_matrix, f1_score, mean_absolute_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight

from .dataset import ROOT, digest, write_json
from .features import EXTRACTOR_HASH
from .ridge_model import FEATURES, PROMPT_VERSION, VERSION, classes, feature_row


def prepared_frame(rows, cache, model, *, allow_quarantined=False):
    records = []
    missing = []
    quarantined = []
    for row in rows:
        assessment = cache.get(row, model)
        if assessment is None:
            error = cache.failure(row,model)
            if allow_quarantined and error:
                quarantined.append({'pair_id':row['pair_id'], 'split':row['split'], 'error':error})
                continue
            missing.append(row['pair_id'])
            continue
        records.append({
            'pair_id': row['pair_id'], 'story_group': row['story_group'],
            'headline_id': row['story_group'], 'position_id': row['position_id'],
            'split': row['split'], 'category': row['category'], 'platform': row['platform'],
            'outcome': row['outcome'], 'sentiment': int(row['current_sentiment']),
            'label_source': row['current_label_origin'], 'related': assessment.related,
            'confidence': assessment.confidence, **assessment.features,
            'evidence_model': assessment.model, 'prompt_version': assessment.prompt_version,
            'reason': assessment.reason, 'evidence': json.dumps(assessment.evidence),
        })
    if missing:
        raise ValueError(f'{len(missing)} evidence rows are missing. Resume extraction before fitting.')
    frame = pd.DataFrame(records)
    frame.attrs['quarantined'] = quarantined
    return frame


def metrics(actual, scores):
    predicted = classes(scores)
    return {
        'rows': len(actual), 'macro_f1': float(f1_score(actual, predicted, labels=[-1, 0, 1], average='macro', zero_division=0)),
        'mae': float(mean_absolute_error(actual, scores)),
        'confusion_matrix_label_order': [-1, 0, 1],
        'confusion_matrix': confusion_matrix(actual, predicted, labels=[-1, 0, 1]).tolist(),
        'per_class': classification_report(actual, predicted, labels=[-1, 0, 1], output_dict=True, zero_division=0),
    }


def evaluate(model, frame, majority, unavailable=0):
    if frame.empty:
        return {'rows': 0, 'candidate_rows':unavailable, 'unavailable_evidence_rows':unavailable, 'coverage_after_abstention':0.0}
    # Conditional Ridge metrics and whole-system coverage are kept distinct.
    relevant = frame[frame.related]
    total = len(frame) + unavailable
    report = {'candidate_rows': total, 'unavailable_evidence_rows':unavailable, 'muse_related_rows': len(relevant),
              'muse_related_coverage': len(relevant) / total,
              'muse_rejected_provisional_related': int((~frame.related).sum()),
              'metric_truth': 'provisional labels; not independently verified accuracy'}
    if relevant.empty:
        return report
    x, y = relevant[list(FEATURES)].to_numpy(), relevant.sentiment.to_numpy()
    scores = model.predict(x)
    report['ridge_conditional'] = metrics(y, scores)
    report['majority_baseline'] = metrics(y, np.full(len(y), majority))
    report['feature_difference_baseline'] = metrics(y, x[:, 1] - x[:, 0])
    confident = ((relevant.confidence >= .8) & (relevant.uncertainty < .5) & (relevant.event_specificity >= .5) & (relevant.time_alignment >= .5)).to_numpy()
    report['abstention_rows'] = total - int(confident.sum())
    report['coverage_after_abstention'] = float(confident.sum() / total)
    if confident.any():
        report['accepted_conditional'] = metrics(y[confident], scores[confident])
    slices = {}
    for name in ['category', 'platform', 'outcome']:
        slices[name] = {}
        for value in sorted(relevant[name].unique()):
            mask = (relevant[name] == value).to_numpy()
            slices[name][str(value)] = {**metrics(y[mask], scores[mask]), 'small_sample': int(mask.sum()) < 30}
    report['slices'] = slices
    return report


def fit_model(frame, *, dataset_report, output, report_path, phase):
    output, report_path = Path(output), Path(report_path)
    fingerprint = digest({'dataset': dataset_report['sha256'], 'pair_ids': sorted(frame.pair_id), 'extractor': EXTRACTOR_HASH, 'phase': phase, 'code': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}})
    if output.exists() or report_path.exists():
        if output.exists() and report_path.exists():
            previous = json.loads(report_path.read_text())
            if previous.get('run_fingerprint') == fingerprint:
                return previous
        raise ValueError('Training output exists with a different configuration. Choose a new artifact/report path.')
    if frame.duplicated('pair_id').any():
        raise ValueError('Duplicate training pair identity.')
    group_splits = frame.groupby('story_group')['split'].nunique()
    if (group_splits > 1).any():
        raise ValueError('Story group crosses splits.')
    train = frame[(frame.split == 'train') & frame.related].copy()
    counts = train.sentiment.value_counts()
    if len(train) < 60 or set(counts.index) != {-1, 0, 1} or counts.min() < 10 or train.story_group.nunique() < 30:
        raise ValueError('Need at least 60 extracted relevant training rows, 10 per class and 30 story groups.')
    if train.evidence_model.nunique() != 1 or set(train.prompt_version) != {PROMPT_VERSION}:
        raise ValueError('Mixed extractor/model versions.')
    x = np.asarray([feature_row(r) for r in train.to_dict('records')])
    y = train.sentiment.to_numpy()
    folds = list(GroupKFold(3).split(x, y, train.story_group))
    started = time.monotonic()
    trials = []
    for alpha in np.logspace(-3, 3, 7):
        scores = []
        for fitting, validation in folds:
            if set(y[fitting]) != {-1, 0, 1}:
                raise ValueError('A grouped training fold is missing a class.')
            estimator = make_pipeline(StandardScaler(), Ridge(alpha=float(alpha)))
            estimator.fit(x[fitting], y[fitting], ridge__sample_weight=compute_sample_weight('balanced', y[fitting]))
            scores.append(float(f1_score(y[validation], classes(estimator.predict(x[validation])), labels=[-1, 0, 1], average='macro', zero_division=0)))
        trials.append({'alpha': float(alpha), 'fold_macro_f1': scores, 'mean_macro_f1': float(np.mean(scores))})
    best = max(trials, key=lambda r: r['mean_macro_f1'])
    estimator = make_pipeline(StandardScaler(), Ridge(alpha=best['alpha']))
    estimator.fit(x, y, ridge__sample_weight=compute_sample_weight('balanced', y))
    majority = int(train.sentiment.mode().iloc[0])
    report = {
        'phase': phase, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'run_fingerprint': fingerprint, 'dataset': dataset_report, 'train_rows': len(train),
        'train_story_groups': int(train.story_group.nunique()), 'train_class_counts': {str(k): int(v) for k, v in counts.items()},
        'train_muse_rejected_rows': int(((frame.split == 'train') & ~frame.related).sum()),
        'alpha': best['alpha'], 'grouped_cv': trials, 'fit_seconds': round(time.monotonic() - started, 3),
        'evidence_model': str(train.evidence_model.iloc[0]), 'prompt_version': PROMPT_VERSION,
        'extractor_hash': EXTRACTOR_HASH, 'features': list(FEATURES),
        'thresholds': {'adverse_below': -.5, 'favorable_above': .5},
        'standardized_feature_coefficients': dict(zip(FEATURES, estimator.named_steps['ridge'].coef_.tolist())),
        'intercept': float(estimator.named_steps['ridge'].intercept_),
        'versions': {p: importlib.metadata.version(p) for p in ['scikit-learn', 'numpy', 'pandas', 'joblib']},
        'experimental': True, 'production_ready': False,
        'quarantined_extraction_rows': frame.attrs.get('quarantined',[]),
        'limits': ['Provisional AI/user labels, not human gold.', 'Historical publisher text and rule versions unverified.', 'Original grouped split is not chronological; rubric was revised after audits.', 'Pilot is deliberately class balanced; its CV does not estimate population accuracy.', 'No trading performance or calibrated probability claim.'],
    }
    # Selection is frozen before reading development/final labels here. There is
    # no threshold search, final-label refit, or repeated final evaluation loop.
    for split in ['dev', 'final']:
        unavailable = sum(r['split']==split for r in frame.attrs.get('quarantined',[]))
        report[split] = evaluate(estimator, frame[frame.split == split], majority, unavailable)
    dev = report['dev']
    report['prototype_checks'] = {
        'split_integrity_passed': True,
        'dev_beats_majority': bool(dev.get('ridge_conditional', {}).get('macro_f1', -1) > dev.get('majority_baseline', {}).get('macro_f1', 0)),
        'human_gold_validation': False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    artifact = {'version': VERSION, 'features': list(FEATURES), 'prompt_version': PROMPT_VERSION,
                'evidence_model': report['evidence_model'], 'extractor_hash': EXTRACTOR_HASH,
                'model': estimator, 'report': report, 'experimental': True}
    joblib.dump(artifact, temp)
    temp.replace(output)
    report['artifact'] = str(output.resolve())
    report['artifact_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    write_json(report_path, report)
    return report
