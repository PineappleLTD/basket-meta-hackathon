"""Portable model installation with an explicit, separate data workspace."""
import json
import os
from pathlib import Path

MODEL_ROOT = Path(__file__).resolve().parents[1]
config = MODEL_ROOT / 'workspace_paths.json'
saved = json.loads(config.read_text()) if config.is_file() else {}
DATA_ROOT = Path(os.environ.get('MUSE_DATA_DIR') or saved.get('data_workspace') or MODEL_ROOT).resolve()


def active_model():
    """Resolve repository-relative model pointers independently of the caller's cwd."""
    pointer = json.loads((MODEL_ROOT / 'models/active.json').read_text())
    artifact = Path(pointer['artifact'])
    if not artifact.is_absolute():
        artifact = MODEL_ROOT / artifact
    return artifact, pointer


def load_env(path):
    if not Path(path).is_file():
        return
    allowed = {'MODEL_API_KEY', 'MUSE_MODEL', 'NEWS_API_KEY', 'MUSE_UPDATES_TOKEN', 'RIDGE_MODEL_PATH'}
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key.strip() in allowed:
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
