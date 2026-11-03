"""Verify the bundled trained model works away from the developer's filesystem."""
import hashlib
import json

from headline_model.paths import MODEL_ROOT, active_model
from headline_model.predictor import Predictor


def test_relative_model_pointer_loads_saved_preprocessing_from_any_cwd(tmp_path, monkeypatch):
    artifact, pointer = active_model()
    assert artifact.is_relative_to(MODEL_ROOT)
    assert not pointer['artifact'].startswith('/')
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == pointer['sha256']
    class NoNetwork:
        model = 'muse-spark-1.3'
        def assess(self, *args):
            raise AssertionError('Packaging validation must never call the provider')
    monkeypatch.chdir(tmp_path)
    predictor = Predictor(cache_path=tmp_path/'evidence.sqlite3', client=NoNetwork())
    try:
        assert predictor.artifact_hash == pointer['sha256']
        assert predictor.scorer.model.named_steps['standardscaler'].n_features_in_ == 5
    finally:
        predictor.close()


def test_deployment_artifact_has_no_private_local_paths():
    artifact, _ = active_model()
    assert b'/Users/' not in artifact.read_bytes()
    provenance = json.loads((artifact.parent/'PROVENANCE.json').read_text())
    assert provenance['source_artifact_sha256'] == 'a138737684e87d53ca35055e809f721ec9c7e354c1d9f0365e549c372b87ac3a'
    assert provenance['prediction_parity']['exactly_equal']
    assert not provenance['retrained'] and not provenance['final_labels_evaluated']
