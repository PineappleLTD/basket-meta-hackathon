"""Protect the production database and secrets during a one-time host migration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[2] / "deploy" / "oracle" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_database_tools_connect_to_same_host_without_credentials_in_arguments(monkeypatch):
    module = load_script("copy-database.py")
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(module.subprocess, "run", run)
    url = "postgresql://basket_owner:pass%24word%40host@source.neon.tech/basket?sslmode=require&channel_binding=require"
    for command in (("psql",), ("pg_dump",), ("pg_restore", "--dbname=basket")):
        module.run_client(url, "private-network", *command)
    for arguments, environment in calls:
        assert url not in arguments
        assert all("pass" not in argument for argument in arguments)
        assert environment["PGHOST"] == "source.neon.tech"
        assert environment["PGDATABASE"] == "basket"
        assert environment["PGUSER"] == "basket_owner"
        assert environment["PGPASSWORD"] == "pass$word@host"
        assert environment["PGSSLMODE"] == "require"
        assert environment["PGCHANNELBINDING"] == "require"


def test_private_database_credentials_are_preserved_on_rerun(tmp_path):
    module = load_script("prepare-database.py")
    directory = tmp_path / "private"
    assert module.prepare(directory)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert not module.prepare(directory)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    (directory / "database-url").unlink()
    with pytest.raises(RuntimeError, match="Incomplete"):
        module.prepare(directory)
    assert (directory / "postgres.env").read_bytes() == before["postgres.env"]


@pytest.fixture
def copy_context(tmp_path, monkeypatch):
    module = load_script("copy-database.py")
    config = tmp_path / "private"
    config.mkdir()
    (config / "runtime.env").write_text("PMETF_DATABASE_URL=postgresql://owner:secret@source.neon.tech/basket?sslmode=require\n")
    (config / "database-url").write_text("postgresql://basket_owner:secret@database:5432/basket\n")
    monkeypatch.setattr(sys, "argv", ["copy-database.py", "--config-dir", str(config),
                                     "--backup-dir", str(tmp_path / "backups"), "--source-quiesced"])
    return module, config


def test_copy_refuses_existing_destination_before_dump_or_restore(copy_context, monkeypatch):
    module, config = copy_context
    monkeypatch.setattr(module, "tables", lambda *args: [["shared", "users"]])
    monkeypatch.setattr(module, "run_client", lambda *args, **kwargs: pytest.fail("must not copy"))
    with pytest.raises(SystemExit):
        module.main()
    assert not (config / "local-postgres.enabled").exists()


def test_failed_row_verification_never_activates_destination(copy_context, monkeypatch):
    module, config = copy_context
    table_results = iter([[], [["shared", "users"]], [["shared", "users"]]])
    count_results = iter([{"shared.users": "2"}, {"shared.users": "1"}])
    monkeypatch.setattr(module, "tables", lambda *args: next(table_results))
    monkeypatch.setattr(module, "counts", lambda *args: next(count_results))
    monkeypatch.setattr(module, "run_client", lambda *args, **kwargs: "")
    with pytest.raises(RuntimeError, match="verification failed"):
        module.main()
    assert not (config / "local-postgres.enabled").exists()
    assert list((config.parent / "backups").glob("*.dump"))
