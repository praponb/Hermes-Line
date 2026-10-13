from pathlib import Path

from conftest import ROOT

from finops import config


def test_db_path_from_config(monkeypatch):
    monkeypatch.delenv("FINOPS_DB_PATH", raising=False)
    cfg = config.load(ROOT / "config" / "auditor.toml")
    assert cfg.db_path == Path("~/.hermes/audit/lakehouse_finops.db").expanduser()


def test_db_path_env_override(monkeypatch):
    monkeypatch.setenv("FINOPS_DB_PATH", "/opt/data/audit/lakehouse_finops.db")
    cfg = config.load(ROOT / "config" / "auditor.toml")
    assert cfg.db_path == Path("/opt/data/audit/lakehouse_finops.db")
