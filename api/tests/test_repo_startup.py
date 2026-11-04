"""The supported launcher must not fall back to a stale editable engine."""
import os
from pathlib import Path
import subprocess
import sys


def test_launcher_prefers_bundled_engine_from_another_working_directory(tmp_path):
    stale = tmp_path / "stale"
    for name in ("news_agent", "headline_model"):
        package = stale / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('Stale engine was imported')\n")
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_api.py"
    result = subprocess.run([sys.executable, str(script), "--check-imports"],
                            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(stale)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Bundled news engine verified."
