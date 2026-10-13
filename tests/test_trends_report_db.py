import json
from datetime import timedelta

from finops import db, report, rules, trends
from finops.__main__ import RunRecord, main


def run(run_id, ts, waste, env="Production-Lakehouse"):
    return RunRecord(run_id, ts.strftime(trends.TS_FORMAT), env, 1, 0.0, waste, 1.0)


def test_schema_and_save(now):
    conn = db.connect(":memory:")
    cf = rules.ComputeFlag("databricks", "c1", "x", "RUNNING", [rules.NO_AUTO_TERM, rules.IDLE_TIMEOUT], True,
                           2.7, 5, 5, False, 0, 4, False, 13.5)
    sf = rules.StorageFlag("t", 3000, 30, 10.0, 1, 1.0, "BOTH", True, ["OPTIMIZE t;"])
    db.save_run(conn, run("r1", now, 13.5), [cf], [sf])
    assert conn.execute("SELECT COUNT(*) FROM compute_anomalies WHERE run_id='r1'").fetchone()[0] == 2
    assert conn.execute("SELECT recommended_action FROM storage_hygiene_flags").fetchone()[0] == "BOTH"
    assert db.unique_run_id(conn, "r1") == "r1_2"
    assert db.unique_run_id(conn, "r9") == "r9"


def test_trend_runaway_and_budget(cfg, now):
    conn = db.connect(":memory:")
    for i, w in enumerate([100, 120, 140]):
        db.save_run(conn, run(f"r{i}", now - timedelta(days=i + 1), w), [], [])
    db.save_run(conn, run("old", now - timedelta(days=30), 9999), [], [])
    db.save_run(conn, run("other-env", now - timedelta(days=1), 9999, env="x"), [], [])
    t = trends.compute(conn, cfg, "cur", 184.2, now)
    assert t.baseline_usd == 120 and t.baseline_runs == 3
    assert t.pct_change == 53.5
    assert t.runaway and t.over_budget
    t = trends.compute(conn, cfg, "cur", 50, now)
    assert not t.runaway and not t.over_budget and t.pct_change < 0


def test_trend_without_baseline(cfg, now):
    t = trends.compute(db.connect(":memory:"), cfg, "cur", 10, now)
    assert t.baseline_usd is None and t.pct_change is None and not t.runaway


def _input(compute=(), storage=(), notes=(), waste=0.0):
    trend = trends.Trend(waste, None, 0, None, False, False)
    return report.ReportInput("Production-Lakehouse", "Apple M2", "run_20261014_0800_arm64", "Tue 14 Oct 08:00",
                              3, 3, list(compute), list(storage), trend, list(notes))


def test_all_clear_is_one_line_plus_notes():
    text = report.render(_input(notes=[report.Note("info", "dataproc:p", "API disabled")]))
    assert text.splitlines()[0].startswith("✅")
    assert "ℹ️ dataproc:p: API disabled" in text


def test_report_content_and_truncation():
    cf = rules.ComputeFlag("databricks", "c1", "analyst-adhoc-sandbox", "RUNNING", [rules.NO_AUTO_TERM], True,
                           2.0, 21, 21, False, 13, 16, True, 42.0)
    sf = rules.StorageFlag("lakehouse_silver.user_events", 14200, 58, 4.2, 0, 0, "OPTIMIZE", True,
                           ["OPTIMIZE lakehouse_silver.user_events;"])
    text = report.render(_input([cf], [sf], waste=42.0))
    assert text.startswith("🚨")
    assert "🔴 analyst-adhoc-sandbox | Single-Node (16 vCPU) | RUNNING (up 21.0h) | No auto-term" in text
    assert "14,200 files (avg 4.2 MB)" in text
    assert "↳ OPTIMIZE lakehouse_silver.user_events;" in text
    many = report.render(_input([cf] * 200, [sf] * 200, waste=42.0))
    assert len(many) <= report.MAX_CHARS
    assert "more in DB" in many


def test_cli_run_end_to_end(tmp_path, cfg, now):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "compute_databricks_lakehouse-audit.json").write_text("[]")
    (run_dir / "storage_lakehouse-audit.json").write_text(json.dumps([
        {"table": "lakehouse.silver.t", "detail": {"numFiles": "3000", "sizeInBytes": str(3000 * rules.MB)},
         "history": []}
    ]))
    (run_dir / "errors.jsonl").write_text(json.dumps({"level": "warn", "target": "dataproc:x", "message": "boom"}) + "\n")
    dbp = tmp_path / "a.db"
    assert main(["run", "--run-dir", str(run_dir), "--db", str(dbp), "--run-id", "run_test"]) == 0
    conn = db.connect(dbp)
    assert conn.execute("SELECT total_storage_flagged_gb FROM audit_runs").fetchone()[0] > 2.9
    assert conn.execute("SELECT table_identifier FROM storage_hygiene_flags").fetchone()[0] == "lakehouse.silver.t"


def test_cli_run_fails_when_nothing_collected(tmp_path, capsys):
    (tmp_path / "errors.jsonl").write_text(json.dumps({"target": "databricks:p", "message": "auth"}) + "\n")
    assert main(["run", "--run-dir", str(tmp_path), "--db", str(tmp_path / "a.db")]) == 2
    assert "databricks:p: auth" in capsys.readouterr().err


def test_targets_tsv(capsys):
    assert main(["targets"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "databricks\tlakehouse-audit\tb8da7ee15120a028\tlakehouse.bronze lakehouse.silver lakehouse.gold"
    assert lines[1] == "dataproc\tpraponb-lakehouse\tasia-southeast1"
