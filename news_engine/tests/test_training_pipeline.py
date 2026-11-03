from dataclasses import asdict
from datetime import timedelta
import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from headline_model.dataset import digest, load_dataset, model_input, pilot_rows
from headline_model.features import FeatureCache, extract_rows, key_for
from headline_model.muse_matcher import validate_assessment
from headline_model.predictor import Predictor
from headline_model.ridge_model import FEATURES, PROMPT_VERSION
from headline_model.training import evaluate, fit_model, prepared_frame
from news_agent.core import Store
from news_agent.scoring import score_staged
from test_news_agent import NOW, TOPIC, article


def row(n=0, split='train', label=0):
    return dict(pair_id=str(n), article_id='article-' + str(n), source_group='source-' + str(n),
                story_group='group-' + str(n), split=split, headline='Federal Reserve discusses interest rates',
                summary='Source feed excerpt.', published_at=(NOW-timedelta(minutes=5)).isoformat(),
                market_question='Will the Federal Reserve reduce interest rates?', position_thesis='A reduction occurs.',
                resolution_rules='YES if the Federal Reserve reduces the target interest rate before December 2026.',
                deadline='2026-12-01T00:00:00Z', outcome='YES', position_id=str(n), category='Economics', platform='kalshi',
                current_related='true', current_sentiment=str(label), current_label_origin='AI',
                audit_exposed_story_group='false', current_timing_screen='No exclusion flagged')


def assessment(r, value=0):
    return validate_assessment(dict(related=True, confidence=.95, reason='Exact rate event.', evidence=[r['headline']],
                              adverse_evidence=max(-value, 0), favorable_evidence=max(value, 0),
                              uncertainty=.1, event_specificity=.95, time_alignment=.9), r, 'fixture-model')


def fixture_frame():
    # Deliberately synthetic math fixture, never a product artifact.
    result = []
    for n in range(120):
        value = [-1, 0, 1][n % 3]
        result.append(dict(pair_id=str(n), position_id=str(n), story_group=str(n), split='train' if n < 90 else 'dev',
                      category='fixture', platform='kalshi', outcome='YES', sentiment=value, related=True, confidence=.95,
                      evidence_model='fixture-model', prompt_version=PROMPT_VERSION, **assessment(row(n), value).features))
    return pd.DataFrame(result)


class PipelineTests(unittest.TestCase):
    def test_combined_project_resolves_packages_config_and_existing_state(self):
        import headline_model
        import news_agent
        from headline_model.paths import MODEL_ROOT, DATA_ROOT
        from news_agent.__main__ import parser
        self.assertEqual(Path(headline_model.__file__).resolve().parents[1], MODEL_ROOT)
        self.assertEqual(Path(news_agent.__file__).resolve().parents[1], MODEL_ROOT)
        args = parser().parse_args(['collect','--watchlist',str(MODEL_ROOT/'watchlist.example.json'),'--requests','1'])
        self.assertEqual(args.state, DATA_ROOT/'state/agent.sqlite3')
        self.assertEqual(args.env, MODEL_ROOT/'.env')
        self.assertTrue(args.feeds.is_file())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_model_inputs_never_contain_targets_or_audit_rationales(self):
        r = row()
        r.update(sentiment='-1', current_rationale='secret answer', current_sentiment='1', ai_rationale='answer', winner='YES')
        self.assertEqual(set(model_input(r)), {'headline','summary','published_at','market_question','resolution_rules','resolution_deadline','held_outcome','held_position_thesis'})
        self.assertNotIn('secret answer', json.dumps(model_input(r)))

    def test_cache_ignores_label_changes_but_invalidates_source_contract_side_or_model(self):
        r = row()
        self.assertEqual(key_for(r, 'm'), key_for({**r, 'current_sentiment': '1'}, 'm'))
        for field in ['headline', 'summary', 'resolution_rules', 'outcome', 'deadline']:
            self.assertNotEqual(key_for(r, 'm'), key_for({**r, field: r[field]+'changed'}, 'm'))
        self.assertNotEqual(key_for(r, 'm'), key_for(r, 'm2'))

    def test_resume_reuses_real_validated_features_without_a_second_call(self):
        calls = []
        class Client:
            model = 'fixture-model'
            def assess(self, article, position):
                calls.append(1)
                return assessment(row())
        cache = FeatureCache(self.path/'cache.sqlite3')
        try:
            first = extract_rows([row()], cache, workers=1, max_calls=1, client_factory=Client)
            second = extract_rows([row()], cache, workers=1, max_calls=1, client_factory=Client)
            self.assertTrue(first['complete'] and second['complete'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(second['cached_at_start'], 1)
        finally:
            cache.close()

    def test_permanent_provider_failure_stops_new_work_and_redacts_details(self):
        calls = []
        class Client:
            model = 'fixture-model'
            def assess(self, article, position):
                calls.append(1)
                raise ValueError('Meta API returned HTTP 401; no decision made.')
        cache = FeatureCache(self.path/'cache.sqlite3')
        try:
            result = extract_rows([row(n) for n in range(20)], cache, workers=1, max_calls=20, client_factory=Client)
            self.assertEqual(len(calls), 1)
            self.assertFalse(result['complete'])
        finally:
            cache.close()

    def test_invalid_output_is_quarantined_without_fabricated_features_or_repeated_calls(self):
        bad = {**row(1), 'market_question':'bad output fixture'}
        good = row(2)
        calls = []
        class Client:
            model = 'fixture-model'
            def assess(self, article, position):
                calls.append(position.question)
                if position.question.startswith('bad'):
                    raise ValueError('Invalid Muse evidence output; no decision made.')
                return assessment(good)
        cache = FeatureCache(self.path/'cache.sqlite3')
        try:
            result = extract_rows([bad,good],cache,workers=1,max_calls=2,client_factory=Client)
            self.assertTrue(result['processed_all'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['quarantined_total'],1)
            self.assertIsNone(cache.get(bad,'fixture-model'))
            with self.assertRaises(ValueError):
                prepared_frame([bad,good],cache,'fixture-model')
            frame = prepared_frame([bad,good],cache,'fixture-model',allow_quarantined=True)
            self.assertEqual(list(frame.pair_id),['2'])
            self.assertEqual(frame.attrs['quarantined'][0]['pair_id'],'1')
            again = extract_rows([bad,good],cache,workers=1,max_calls=2,client_factory=Client)
            self.assertTrue(again['processed_all'])
            self.assertEqual(len(calls),2)
        finally:
            cache.close()

    def test_missing_evidence_is_counted_as_abstention_in_evaluation_coverage(self):
        frame = fixture_frame()
        dev = frame[frame.split=='dev']
        model = SimpleNamespace(predict=lambda x: np.zeros(len(x)))
        report = evaluate(model,dev,0,unavailable=5)
        self.assertEqual(report['candidate_rows'],35)
        self.assertEqual(report['unavailable_evidence_rows'],5)
        self.assertEqual(report['abstention_rows'],5)
        self.assertAlmostEqual(report['coverage_after_abstention'],30/35)

    def test_dataset_rejects_a_story_crossing_splits(self):
        rows = [row(1), {**row(2, 'final'), 'story_group': 'group-1'}]
        p = self.path/'data.csv'
        with p.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
        with self.assertRaisesRegex(ValueError, 'crosses'):
            load_dataset(p)

    def test_timing_and_missing_labels_are_excluded_without_becoming_neutral(self):
        rows = [row(1), {**row(2), 'current_sentiment': ''}, {**row(3), 'current_timing_screen': 'Unsure'}]
        p = self.path/'data.csv'
        with p.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
        eligible, excluded, audit = load_dataset(p)
        self.assertEqual([r['pair_id'] for r in eligible], ['1'])
        self.assertEqual(len(excluded), 2)

    def test_pilot_never_draws_development_or_final_rows(self):
        rows = [row(n, 'train', [-1,0,1][n%3]) for n in range(90)] + [row(100+n, 'final', [-1,0,1][n%3]) for n in range(90)]
        result = pilot_rows(rows, 10)
        self.assertEqual(len(result), 30)
        self.assertEqual({r['split'] for r in result}, {'train'})

    def test_fitting_excludes_dev_final_labels_and_preserves_existing_artifact(self):
        frame = fixture_frame()
        p = self.path/'a.joblib'
        audit = {'sha256':'fixture'}
        a = fit_model(frame, dataset_report=audit, output=p, report_path=self.path/'a.json', phase='test')
        altered = frame.copy()
        altered.loc[altered.split == 'dev', 'sentiment'] = 0
        b = fit_model(altered, dataset_report=audit, output=self.path/'b.joblib', report_path=self.path/'b.json', phase='test')
        self.assertEqual(a['alpha'], b['alpha'])
        self.assertEqual(a['standardized_feature_coefficients'], b['standardized_feature_coefficients'])
        self.assertEqual(a['train_rows'], 90)
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            fit_model(frame, dataset_report={'sha256':'changed'}, output=p, report_path=self.path/'a.json', phase='test')
        self.assertEqual(before, p.read_bytes())

    def test_predictor_uses_the_saved_scaler_and_cached_evidence(self):
        frame = fixture_frame()
        p = self.path/'model.joblib'
        fit_model(frame, dataset_report={'sha256':'fixture'}, output=p, report_path=self.path/'report.json', phase='test')
        cache = FeatureCache(self.path/'cache.sqlite3')
        r = row()
        # Use the exact runtime contract fields so the cached request identity matches.
        contract = dict(platform='kalshi', outcome='YES', question=r['market_question'], resolution_rules=r['resolution_rules'], deadline=r['deadline'], thesis=r['position_thesis'])
        cache.put(r, assessment(r)); cache.close()
        class Client:
            model = 'fixture-model'
            def assess(self, *args):
                raise AssertionError('Cache hit must not call the provider')
        predictor = Predictor(p, cache_path=self.path/'cache.sqlite3', client=Client())
        try:
            result = predictor.predict(article().__dict__, contract, now=NOW)
            self.assertTrue(result['feature_cache_hit'])
            self.assertEqual(result['classification'], 'NEUTRAL')
            expired = predictor.predict(article().__dict__, contract, now=NOW+timedelta(days=200))
            self.assertEqual(expired['classification'], 'ABSTAIN')
        finally:
            predictor.close()

    def test_scoring_is_idempotent_and_checks_each_matching_contract(self):
        store = Store(self.path/'agent.sqlite3')
        ident = store.record_article(article()); store.stage(ident, TOPIC['id'], 'Relevant')
        topics = [{**TOPIC, 'id':str(n), 'contract':{'platform':'kalshi','outcome':'YES','question': 'Rates?', 'resolution_rules':'Rates rule', 'deadline':'2026-12-01T00:00:00Z'}} for n in range(2)]
        class P:
            artifact_hash = 'fixture'
            calls = 0
            def predict(self, article, contract, now=None):
                self.calls += 1
                return {'classification':'NEUTRAL'}
        predictor = P()
        try:
            first = score_staged(store, topics, model_calls=3, predictor=predictor, clock=lambda: NOW)
            second = score_staged(store, topics, model_calls=3, predictor=predictor, clock=lambda: NOW)
            self.assertEqual(len(first['scored']), 2)
            self.assertEqual(second['reused'], 2)
            self.assertEqual(predictor.calls, 2)
        finally:
            store.close()

    def test_muse_selected_newsapi_topic_can_score_without_literal_alias_match(self):
        store = Store(self.path/'agent.sqlite3')
        ident = store.record_article(article()); store.stage(ident, 'selected', 'Verified by discovery agent')
        topic = {**TOPIC, 'id':'selected', 'alias_groups':[['Different wording']], 'contract':{'deadline':'2026-12-01T00:00:00Z'}}
        class P:
            artifact_hash = 'fixture'
            calls = 0
            def predict(self, *args, **kwargs):
                self.calls += 1
                return {'classification':'NEUTRAL'}
        try:
            result = score_staged(store, [topic], model_calls=1, predictor=P(), clock=lambda: NOW)
            self.assertEqual(len(result['scored']), 1)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
