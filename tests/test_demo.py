import importlib.util
from datetime import datetime, timezone

from finops import db, web
from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("make_demo", ROOT / "demo" / "make_demo.py")
make_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_demo)

# Thu 22 Oct 2026 09:00 Bangkok time
NOW = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)


def test_demo_builds_runs_with_findings(tmp_path):
    latest = make_demo.build(tmp_path, NOW)
    cfg = make_demo.config_mod.load(tmp_path / "demo.toml")
    assert cfg.environment_name == "Demo-Lakehouse"
    d = web.load(db.connect_readonly(tmp_path / "demo.db"), cfg, NOW)
    assert len(d.runs) == 16  # weekdays 1–22 Oct
    assert d.compute and d.storage
    assert latest.trend.over_budget and latest.trend.runaway
    assert sum(r.waste_usd > cfg.thresholds.daily_waste_alert_usd for r in d.runs) == 6
