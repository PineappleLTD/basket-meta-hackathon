"""Explicit historical simulation; original source dates are never rewritten."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile

from .dataset import ROOT, load_dataset, pilot_rows, write_json
from .engine import utc
from .predictor import Predictor


def replay(*, limit=6, model=None, use_agent=True, output=None):
    if not 1 <= limit <= 20:
        raise ValueError('Choose 1–20 historical replay rows.')
    rows, _, audit = load_dataset()
    # Explicitly use the pilot training set; no final/development exposure.
    pilot = pilot_rows(rows, 50)
    selected = []
    labels = ['-1', '0', '1']
    for n in range(limit):
        pool = [r for r in pilot if r['current_sentiment'] == labels[n % 3]]
        selected.append(pool[n // 3])
    predictor = Predictor(model, max_calls=limit)
    report = {'mode': 'HISTORICAL_REPLAY', 'timestamp_policy': 'Original timestamps retained; agent clock simulated at publication plus one minute.',
              'split': 'train', 'dataset_sha256': audit['sha256'], 'rows': [], 'external_delivery': False}
    try:
        if use_agent:
            # The model package remains independent for training and prediction.
            # The optional replay demonstration imports the separately installed agent.
            from news_agent.agent import run_agent
            from news_agent.core import Store, Article
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/'replay.sqlite3') if use_agent else None
            try:
                for r in selected:
                    article = {'headline': r['headline'], 'summary': r['summary'], 'published_at': r['published_at'], 'url': r['source_url'], 'source': r['publisher']}
                    contract = {'platform': r['platform'], 'market_id': r['market_id'], 'question': r['market_question'], 'thesis': r['position_thesis'], 'outcome': r['outcome'], 'resolution_rules': r['resolution_rules'], 'deadline': r['deadline']}
                    entry = {'pair_id': r['pair_id'], 'headline': r['headline'], 'published_at': r['published_at'], 'market_question': r['market_question'], 'held_outcome': r['outcome'], 'deadline': r['deadline'], 'provisional_dataset_label': int(r['current_sentiment'])}
                    if use_agent:
                        aid = store.record_article(Article.parse(article))
                        topic = {'id': r['pair_id'], 'category': r['category'], 'query': r['headline'][:450], 'alias_groups': [[r['headline']]], 'contract': contract}
                        simulated_time = utc(r['published_at']) + timedelta(minutes=1)
                        entry['discovery'] = run_agent(store, [topic], [{'article_id': aid, 'topic_ids': [topic['id']], **article}], model_budget=3, tool_budget=3, clock=lambda: simulated_time)
                        entry['staged'] = aid in entry['discovery']['staged']
                    entry['prediction'] = predictor.predict(article, contract, historical=True)
                    report['rows'].append(entry)
            finally:
                if store:
                    store.close()
        report['new_evidence_calls'] = predictor.calls
        write_json(output or ROOT/'reports/training/historical-replay.json', report)
        return report
    finally:
        predictor.close()
