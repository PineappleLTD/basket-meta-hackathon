"""Ridge learns a held-position sentiment score from Muse-extracted evidence.

No dataset is loaded or fitted automatically. Scores are not probabilities.
"""
from pathlib import Path
import re

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import f1_score, make_scorer, mean_absolute_error
from sklearn.model_selection import GridSearchCV, GroupKFold, GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight

FEATURES = ('adverse_evidence', 'favorable_evidence', 'uncertainty', 'event_specificity', 'time_alignment')
VERSION = 2
PROMPT_VERSION = 'position-evidence-v1'


def normalize(text):
    return ' '.join(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", str(text).lower()))


def feature_row(values):
    row = np.asarray([values[name] for name in FEATURES], dtype=float)
    if not np.isfinite(row).all() or (row < 0).any() or (row > 1).any():
        raise ValueError('Evidence features must be finite numbers between 0 and 1.')
    return row


def classes(values):
    return np.where(np.asarray(values) < -0.5, -1, np.where(np.asarray(values) > 0.5, 1, 0))


def macro_f1(actual, predicted):
    return f1_score(actual, classes(predicted), labels=[-1, 0, 1], average='macro', zero_division=0)


class PositionRidge:
    def __init__(self, model_path=None):
        self.model = None
        self.evidence_model = None
        if model_path is not None:
            # Only load locally generated, trusted artifacts: joblib uses pickle.
            artifact = joblib.load(model_path)
            if artifact.get('version') != VERSION or artifact.get('features') != list(FEATURES) or artifact.get('prompt_version') != PROMPT_VERSION:
                raise ValueError('Incompatible Ridge artifact or evidence prompt version.')
            self.model = artifact['model']
            self.evidence_model = artifact['evidence_model']

    def score(self, assessment):
        if self.model is None:
            raise ValueError('No trained Ridge model. Review your dataset before training.')
        if assessment.model != self.evidence_model or assessment.prompt_version != PROMPT_VERSION:
            raise ValueError('Muse model differs from training. Re-extract and validate features before changing models.')
        value = float(self.model.predict(feature_row(assessment.features).reshape(1, -1))[0])
        if not np.isfinite(value):
            raise ValueError('Non-finite Ridge score.')
        return value

    def train(self, csv_path, model_path):
        frame = pd.read_csv(csv_path)
        required = ['headline_id', 'position_id', 'evidence_model', 'prompt_version', *FEATURES, 'sentiment']
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError('Missing training columns: ' + ', '.join(sorted(missing)))
        if frame[required].isna().any().any():
            raise ValueError('Training values cannot be blank.')
        if 'related' in frame and not frame.related.astype(str).str.lower().isin(['true', '1']).all():
            raise ValueError('Exclude unrelated pairs before sentiment training; keep them for relevance evaluation.')
        for key in ['headline_id', 'position_id', 'evidence_model', 'prompt_version']:
            frame[key] = frame[key].astype(str).str.strip()
            if frame[key].eq('').any():
                raise ValueError(f'{key} cannot be blank.')
        if frame.evidence_model.nunique() != 1 or set(frame.prompt_version) != {PROMPT_VERSION}:
            raise ValueError('Use one Muse model and the current evidence prompt version per training artifact.')
        frame.sentiment = pd.to_numeric(frame.sentiment, errors='raise')
        if not frame.sentiment.isin([-1, 0, 1]).all():
            raise ValueError('Labels: -1 adverse to held bet, 0 neutral/unclear, +1 favorable to held bet.')
        if frame.duplicated(['headline_id', 'position_id']).any():
            raise ValueError('Deduplicate headline/position pairs before training.')
        x = np.asarray([feature_row(row) for row in frame.to_dict('records')])
        y = frame.sentiment.to_numpy(dtype=float)
        groups = frame.headline_id
        counts = frame.sentiment.value_counts()
        if len(frame) < 60 or len(counts) != 3 or counts.min() < 10 or groups.nunique() < 30:
            raise ValueError('Need 60 reviewed pairs, 10 per class, and 30 distinct headline groups. These are software minima, not accuracy guarantees.')
        train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(x, y, groups))
        if set(y[train]) != {-1, 0, 1} or set(y[test]) != {-1, 0, 1}:
            raise ValueError('Train/test splits must contain all classes; add more diverse headline groups.')
        cv = list(GroupKFold(3).split(x[train], y[train], groups.iloc[train]))
        pipeline = Pipeline([('scale', StandardScaler()), ('ridge', Ridge())])
        search = GridSearchCV(pipeline, {'ridge__alpha': np.logspace(-3, 3, 7)}, cv=cv, scoring=make_scorer(macro_f1), n_jobs=1, error_score='raise')
        search.fit(x[train], y[train], ridge__sample_weight=compute_sample_weight('balanced', y[train]))
        predicted = search.predict(x[test])
        majority = pd.Series(y[train]).mode().iloc[0]
        report = {
            'train_pairs': len(train), 'test_pairs': len(test),
            'alpha': float(search.best_params_['ridge__alpha']),
            'holdout_macro_f1': float(macro_f1(y[test], predicted)),
            'majority_baseline_macro_f1': float(macro_f1(y[test], np.full(len(test), majority))),
            'holdout_mae': float(mean_absolute_error(y[test], predicted)),
            'evidence_model': str(frame.evidence_model.iloc[0]), 'prompt_version': PROMPT_VERSION,
            'split': 'Distinct headline groups across tuning/holdout; this is not a temporal trading backtest.',
        }
        # Do not refit on held-out labels.
        self.model = search.best_estimator_
        self.evidence_model = str(frame.evidence_model.iloc[0])
        destination = Path(model_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({'version': VERSION, 'features': list(FEATURES), 'prompt_version': PROMPT_VERSION, 'evidence_model': self.evidence_model, 'model': self.model, 'report': report}, destination)
        return report
