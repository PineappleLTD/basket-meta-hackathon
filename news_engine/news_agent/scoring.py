"""Score staged articles against exact contracts without queue delivery or orders."""
from datetime import datetime, timezone
import json

from .core import AgentError


def score_staged(store, topics, *, model_calls, artifact=None, limit=40, predictor=None, clock=None):
    contracts = [t for t in topics if t.get('contract')]
    if not contracts:
        raise AgentError('Scoring requires exact contracts. Supply a portfolio or watchlist with contract rules and YES/NO held sides.')
    if min(model_calls, limit) < 1:
        raise AgentError('Scoring budget and limit must be positive.')
    own = predictor is None
    if own:
        from headline_model.predictor import Predictor
        predictor = Predictor(artifact, max_calls=model_calls)
    clock = clock or (lambda: datetime.now(timezone.utc))
    with store.lock, store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS scores (key TEXT PRIMARY KEY, article_id TEXT NOT NULL, topic_id TEXT NOT NULL, result TEXT NOT NULL)')
        staged = list(store.db.execute("SELECT article_id,topic_id FROM outbox WHERE status IN ('pending','delivered') ORDER BY rowid DESC"))
    results = []
    reused = 0
    max_age = getattr(predictor, 'max_age_seconds', 3600)
    try:
        from headline_model.dataset import digest
        # Only assess exact pairs selected by discovery. Expanding one political
        # headline to every lexical match spent the allowance on unselected work.
        articles = {aid: store.article(aid) for aid, _ in staged}
        by_id = {t['id']: t for t in contracts}
        pairs = [(aid, by_id[tid]) for aid, tid in staged if tid in by_id]
        for article_id, topic in pairs:
            article = articles[article_id]
            key = digest({'article_id': article_id, 'topic_id': topic['id'], 'contract': topic['contract'],
                          'artifact': predictor.artifact_hash, 'max_age_seconds': max_age})
            # Freshness is rechecked even for previously scored records.
            from .core import utc
            if not article.is_fresh(clock(), max_age) or utc(topic['contract']['deadline']) <= clock():
                continue
            with store.lock:
                saved = store.db.execute('SELECT result FROM scores WHERE key=?', (key,)).fetchone()
            if saved:
                reused += 1
                continue
            result = predictor.predict(article.__dict__, topic['contract'], now=clock())
            result.update({'topic_id': topic['id'], 'article_id': article_id})
            if result.get('reason') == 'model_call_budget_reached':
                return {'scored': results, 'reused': reused, 'model_calls': predictor.calls, 'budget_exhausted': True}
            with store.lock, store.db:
                store.db.execute('INSERT OR IGNORE INTO scores VALUES (?,?,?,?)', (key, article_id, topic['id'], json.dumps(result)))
            results.append(result)
            if len(results) >= limit:
                return {'scored': results, 'reused': reused, 'model_calls': predictor.calls, 'limit_reached': True}
        return {'scored': results, 'reused': reused, 'model_calls': predictor.calls, 'execution': 'NOT_CONNECTED'}
    finally:
        if own:
            predictor.close()
