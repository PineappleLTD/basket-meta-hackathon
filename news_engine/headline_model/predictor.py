"""One shared Muse → Ridge inference path for the CLI and news agent."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .records import Article
from .engine import utc
from .paths import MODEL_ROOT, active_model
from .dataset import ROOT, digest
from .features import EXTRACTOR_HASH, FeatureCache
from .muse_matcher import MetaClient
from .ridge_model import PositionRidge, classes


class Predictor:
    def __init__(self, artifact=None, *, cache_path=None, max_calls=1, client=None, max_age_seconds=3600):
        if max_calls < 1:
            raise ValueError('A positive model-call budget is required.')
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 86400:
            raise ValueError('Headline age must be between one second and 24 hours.')
        self.max_age_seconds = max_age_seconds
        active = None
        if artifact is None:
            artifact, active = active_model()
        artifact = Path(artifact)
        self.artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if active and active['sha256'] != self.artifact_hash:
            raise ValueError('Active artifact checksum mismatch.')
        self.scorer = PositionRidge(artifact)
        import joblib
        metadata = joblib.load(artifact)
        if metadata.get('extractor_hash') != EXTRACTOR_HASH:
            raise ValueError('Artifact extraction instructions differ from current code.')
        self.experimental = metadata.get('experimental', True)
        self.client = client or MetaClient()
        if self.client.model != self.scorer.evidence_model:
            raise ValueError('Configured Muse model differs from the trained artifact.')
        self.cache = FeatureCache(cache_path or ROOT / 'state/training/features.sqlite3')
        self.max_calls, self.calls = max_calls, 0

    def close(self):
        self.cache.close()

    def predict(self, article, contract, *, historical=False, now=None):
        article = Article.parse(article)
        if contract.get('platform') not in {'kalshi', 'polymarket'} or contract.get('outcome') not in {'YES', 'NO'}:
            raise ValueError('Use a Kalshi/Polymarket contract with an explicit YES/NO held side.')
        for key in ['question', 'resolution_rules', 'deadline']:
            if not isinstance(contract.get(key), str) or not contract[key].strip():
                raise ValueError('Exact contract question, rules and deadline are required.')
        now = now or datetime.now(timezone.utc)
        deadline = utc(contract['deadline'])
        result = {'article_id': article.id, 'held_outcome': contract['outcome'],
                  'artifact_sha256': self.artifact_hash, 'evidence_model': self.scorer.evidence_model,
                  'experimental': self.experimental, 'historical': historical,
                  'score_is_probability': False, 'execution': 'NOT_CONNECTED',
                  'max_age_seconds': self.max_age_seconds}
        if not historical and (not article.is_fresh(now, self.max_age_seconds) or deadline <= now):
            return {**result, 'classification': 'ABSTAIN', 'reason': 'stale_article_or_expired_contract'}
        if utc(article.published_at) >= deadline:
            return {**result, 'classification': 'ABSTAIN', 'reason': 'article_at_or_after_contract_deadline'}
        row = {'headline': article.headline, 'summary': article.summary, 'published_at': article.published_at,
               'market_question': contract['question'], 'resolution_rules': contract['resolution_rules'],
               'deadline': contract['deadline'], 'outcome': contract['outcome'],
               'position_thesis': contract.get('thesis') or ('The supplied resolution condition occurs.' if contract['outcome'] == 'YES' else 'The supplied resolution condition does not occur.')}
        assessment = self.cache.get(row, self.client.model)
        result['feature_cache_hit'] = assessment is not None
        if assessment is None:
            if self.calls >= self.max_calls:
                return {**result, 'classification': 'ABSTAIN', 'reason': 'model_call_budget_reached'}
            self.calls += 1
            position = SimpleNamespace(question=row['market_question'], thesis=row['position_thesis'], outcome=row['outcome'], resolution_rules=row['resolution_rules'], deadline=row['deadline'])
            assessment = self.client.assess(article.__dict__, position)
            self.cache.put(row, assessment)
        result.update({'related': assessment.related, 'confidence': assessment.confidence,
                       'features': assessment.features, 'evidence': assessment.evidence, 'reason': assessment.reason})
        if not assessment.related:
            return {**result, 'classification': 'UNRELATED'}
        score = self.scorer.score(assessment)
        result['ridge_score'] = score
        if assessment.confidence < .8 or assessment.features['uncertainty'] >= .5 or assessment.features['event_specificity'] < .5 or assessment.features['time_alignment'] < .5:
            return {**result, 'classification': 'ABSTAIN', 'abstention_reason': 'uncertain_or_weak_evidence'}
        return {**result, 'classification': {-1: 'ADVERSE', 0: 'NEUTRAL', 1: 'FAVORABLE'}[int(classes([score])[0])]}
