import threading
import urllib.error
import urllib.request
from datetime import timedelta

import pytest

from finops import db, rules, web
from tests.test_trends_report_db import run


def _seed(path, now):
    conn = db.connect(path)
    db.save_run(conn, run("r_old", now - timedelta(days=40), 999), [], [])
    db.save_run(conn, run("r1", now - timedelta(days=2), 20.0), [], [])
    cf = rules.ComputeFlag("databricks", "c1", "<script>x</script>", "RUNNING", [rules.NO_AUTO_TERM, rules.IDLE_TIMEOUT],
                           True, 2.7, 5, 5, False, 0, 4, False, 64.8)
    sf = rules.StorageFlag("lakehouse.silver.events", 3000, 30, 10.0, 1, 1.0, "BOTH", True, ["OPTIMIZE t;"])
    db.save_run(conn, run("r2", now - timedelta(hours=1), 150.0), [cf], [sf])
    conn.close()


def test_load_latest_run_and_window(cfg, now, tmp_path):
    path = tmp_path / "a.db"
    _seed(path, now)
    d = web.load(db.connect_readonly(path), cfg, now)
    assert [r.run_id for r in d.runs] == ["r1", "r2"]
    assert len(d.compute) == 1 and d.compute[0].violations.count(",") == 1
    assert d.storage[0].recommended_action == "BOTH"
    assert d.trend.baseline_usd == 20.0 and d.trend.over_budget


def test_page_escapes_and_flags_alert(cfg, now, tmp_path):
    path = tmp_path / "a.db"
    _seed(path, now)
    text = web.page(cfg, path, "prapon@gmail.com", now)
    assert "<script>x</script>" not in text and "&lt;script&gt;" in text
    assert 'class="tile alert"' in text and 'class="bar alert"' in text
    assert "signed in as prapon@gmail.com" in text
    assert "Wed 14 Oct 07:00" in text  # latest run shown in Bangkok time


def test_page_without_db_or_runs(cfg, now, tmp_path):
    assert "No audit runs" in web.page(cfg, tmp_path / "missing.db", None, now)
    db.connect(tmp_path / "empty.db").close()
    assert "No audit runs" in web.page(cfg, tmp_path / "empty.db", None, now)
    assert not (tmp_path / "missing.db").exists()


def test_readonly_connection_cannot_write(tmp_path):
    db.connect(tmp_path / "a.db").close()
    conn = db.connect_readonly(tmp_path / "a.db")
    with pytest.raises(Exception, match="readonly"):
        conn.execute("DELETE FROM audit_runs")


def test_http_routes(cfg, tmp_path):
    server = web.make_server(cfg, tmp_path / "missing.db", "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            assert r.status == 200 and "no-store" in r.headers["Cache-Control"]
            assert "Lakehouse FinOps" in r.read().decode()
        with urllib.request.urlopen(base + "/healthz") as r:
            assert r.read() == b"ok\n"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/etc/passwd")
        assert e.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
