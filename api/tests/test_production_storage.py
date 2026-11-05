"""Migration must use the intended database and preserve private connection data."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_production.py"
    spec = importlib.util.spec_from_file_location("production_storage_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("PMETF_DATABASE_URL", "PMETF_DATABASE_URL_FILE", "BASKET_RUNTIME_USER",
                 "RAILWAY_ENVIRONMENT_ID", "RAILWAY_VOLUME_MOUNT_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASKET_DATA_DIR", str(tmp_path / "data"))
    return module


def test_database_secret_preserves_password_without_environment_expansion(launcher, monkeypatch, tmp_path):
    url = "postgresql://basket_owner:literal$part@database:5432/basket"
    secret = tmp_path / "database-url"
    secret.write_text(url + "\n")
    monkeypatch.setenv("PMETF_DATABASE_URL_FILE", str(secret))
    assert launcher.runtime_environment()["PMETF_DATABASE_URL"] == url


def test_conflicting_database_settings_fail_instead_of_using_wrong_database(launcher, monkeypatch, tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("postgresql://new-host/basket")
    monkeypatch.setenv("PMETF_DATABASE_URL_FILE", str(secret))
    monkeypatch.setenv("PMETF_DATABASE_URL", "postgresql://old-host/basket")
    with pytest.raises(RuntimeError, match="not both"):
        launcher.runtime_environment()


def test_missing_or_empty_database_secret_never_falls_back_to_local_sqlite(launcher, monkeypatch, tmp_path):
    secret = tmp_path / "database-url"
    monkeypatch.setenv("PMETF_DATABASE_URL_FILE", str(secret))
    with pytest.raises(FileNotFoundError):
        launcher.runtime_environment()
    secret.write_text("\n")
    with pytest.raises(RuntimeError, match="empty"):
        launcher.runtime_environment()


def test_existing_hosted_database_configuration_is_preserved(launcher, monkeypatch):
    url = "postgresql://existing-host/basket?sslmode=require"
    monkeypatch.setenv("PMETF_DATABASE_URL", url)
    assert launcher.runtime_environment()["PMETF_DATABASE_URL"] == url
