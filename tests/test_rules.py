import json
from datetime import timedelta

from conftest import ms

from finops import rules


def dbx(now, **over):
    rec = {
        "platform": "databricks",
        "cluster_id": "c1",
        "cluster_name": "analyst-adhoc",
        "state": "RUNNING",
        "cluster_source": "UI",
        "autotermination_minutes": 60,
        "num_workers": 2,
        "node_type_id": "n2-highmem-4",
        "driver_node_type_id": "n2-highmem-4",
        "driver_cores": 4,
        "start_time": ms(now - timedelta(days=30)),
        "last_restarted_time": ms(now - timedelta(minutes=30)),
    }
    rec.update(over)
    return rec


def test_healthy_cluster_not_flagged(cfg, now):
    assert rules.evaluate_compute(dbx(now), now, cfg) is None


def test_no_auto_term_running_is_alert(cfg, now):
    f = rules.evaluate_compute(dbx(now, autotermination_minutes=0), now, cfg)
    assert f.violations == [rules.NO_AUTO_TERM]
    assert f.alert


def test_no_auto_term_terminated_flag_only(cfg, now):
    f = rules.evaluate_compute(dbx(now, autotermination_minutes=0, state="TERMINATED"), now, cfg)
    assert f.violations == [rules.NO_AUTO_TERM]
    assert not f.alert
    assert f.est_daily_waste_usd == 0


def test_job_cluster_without_auto_term_is_normal(cfg, now):
    assert rules.evaluate_compute(dbx(now, autotermination_minutes=0, cluster_source="JOB"), now, cfg) is None


def test_idle_flag_vs_alert_thresholds(cfg, now):
    # restarted 3h ago at 05:00 Bangkok -> also overlaps off-hours
    f = rules.evaluate_compute(dbx(now, last_activity_time=ms(now - timedelta(hours=3))), now, cfg)
    assert rules.IDLE_TIMEOUT in f.violations and not f.alert
    f = rules.evaluate_compute(dbx(now, last_activity_time=ms(now - timedelta(hours=5))), now, cfg)
    assert rules.IDLE_TIMEOUT in f.violations and f.alert
    assert f.idle_is_activity_based


def test_idle_falls_back_to_uptime_since_restart(cfg, now):
    f = rules.evaluate_compute(dbx(now, last_restarted_time=ms(now - timedelta(hours=5))), now, cfg)
    assert rules.IDLE_TIMEOUT in f.violations
    assert f.idle_hours == 5.0
    assert not f.idle_is_activity_based


def test_oversized_single_node(cfg, now):
    f = rules.evaluate_compute(dbx(now, num_workers=0, driver_cores=16, driver_node_type_id="n2-standard-16"), now, cfg)
    assert f.violations == [rules.OVERSIZED_DEV]
    assert f.single_node
    autoscaling = dbx(now, num_workers=0, autoscale_min=0, autoscale_max=4, driver_cores=16)
    assert rules.evaluate_compute(autoscaling, now, cfg) is None


def test_off_hours_overlap(cfg, now):
    # now = 08:00 Bangkok; running since 18:00 yesterday -> 18:00-19:00 business, 19:00-08:00 = 13h off-hours
    start = now - timedelta(hours=14)
    assert rules.off_hours_overlap(start, now, cfg) == 13.0
    f = rules.evaluate_compute(dbx(now, last_restarted_time=ms(start), last_activity_time=ms(now)), now, cfg)
    assert f.violations == [rules.OFF_HOURS]
    # waste = hourly (0.90 driver + 2 x 0.90 workers) x 13 off-hours
    assert f.hourly_rate_usd == 2.7
    assert f.est_daily_waste_usd == round(2.7 * 13, 2)


def test_waste_capped_at_24h(cfg, now):
    f = rules.evaluate_compute(dbx(now, last_restarted_time=ms(now - timedelta(days=3))), now, cfg)
    assert f.est_daily_waste_usd == round(2.7 * 24, 2)


def test_dataproc_rules(cfg, now):
    rec = {
        "platform": "dataproc",
        "cluster_id": "uuid",
        "cluster_name": "etl-sandbox",
        "state": "RUNNING",
        "state_start_time": (now - timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
        "num_workers": 0,
        "master_machine_type": "n2-standard-16",
        "worker_machine_type": None,
        "idle_delete_ttl": None,
    }
    f = rules.evaluate_compute(rec, now, cfg)
    assert f.violations == [rules.DATAPROC_NO_IDLE_TTL, rules.IDLE_TIMEOUT, rules.OVERSIZED_DEV, rules.OFF_HOURS]
    assert f.alert and f.driver_cores == 16
    rec["idle_delete_ttl"] = "3600s"
    rec["master_machine_type"] = "n2-standard-4"
    rec["state_start_time"] = (now - timedelta(minutes=30)).isoformat()
    assert rules.evaluate_compute(rec, now, cfg) is None


def test_machine_cores():
    assert rules.machine_cores("n2-highmem-16") == 16
    assert rules.machine_cores("n2-custom-8-32768") == 8
    assert rules.machine_cores("e2-micro") is None


# ---------------------------------------------------------------- storage


def table(now, files, size_mb, history=(), props=None):
    return {
        "table": "lakehouse.silver.user_events",
        "detail": {
            "numFiles": str(files),
            "sizeInBytes": str(int(size_mb * rules.MB)),
            "properties": json.dumps(props or {}),
        },
        "history": list(history),
    }


def commit(now, days_ago, op, **metrics):
    return {
        "timestamp": (now - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
        "operation": op,
        "operationMetrics": json.dumps({k: str(v) for k, v in metrics.items()}),
    }


def test_small_table_not_flagged(cfg, now):
    # 1 file of 6 KB: tiny avg file size, but nothing to compact
    assert rules.evaluate_storage(table(now, 1, 0.006), now, cfg) is None


def test_optimize_flag_and_alert(cfg, now):
    f = rules.evaluate_storage(table(now, 200, 200 * 50), now, cfg)
    assert f.recommended_action == "OPTIMIZE" and not f.alert
    f = rules.evaluate_storage(table(now, 14200, 14200 * 4.2), now, cfg)
    assert f.alert and f.avg_file_size_mb == 4.2
    assert f.remediation == ["OPTIMIZE lakehouse.silver.user_events;"]


def test_large_files_many_count_flags_optimize(cfg, now):
    f = rules.evaluate_storage(table(now, 1500, 1500 * 256), now, cfg)
    assert f.recommended_action == "OPTIMIZE" and not f.alert


def test_vacuum_from_history(cfg, now):
    history = [
        commit(now, 20, "VACUUM END"),
        commit(now, 15, "OPTIMIZE", numRemovedFiles=400, numRemovedBytes=100 * rules.GB),  # vacuumable
        commit(now, 10, "MERGE", numTargetFilesRemoved=10, numTargetBytesRemoved=40 * rules.GB),  # vacuumable
        commit(now, 9, "WRITE", numFiles=3),  # append, removes nothing
        commit(now, 2, "DELETE", numRemovedFiles=5, numRemovedBytes=rules.GB),  # inside retention
        commit(now, 25, "OPTIMIZE", numRemovedFiles=9, numRemovedBytes=rules.GB),  # before last vacuum
    ]
    f = rules.evaluate_storage(table(now, 10, 10 * 200, history), now, cfg)
    assert f.recommended_action == "VACUUM"
    assert f.stale_snapshots_count == 2
    assert f.reclaimable_space_gb == 140.0
    assert f.remediation == ["VACUUM lakehouse.silver.user_events RETAIN 168 HOURS;"]


def test_vacuum_respects_table_retention(cfg, now):
    history = [commit(now, 10, "DELETE", numRemovedFiles=1, numRemovedBytes=rules.GB)]
    props = {"delta.deletedFileRetentionDuration": "interval 14 days"}
    assert rules.evaluate_storage(table(now, 10, 2000, history, props), now, cfg) is None
    history = [commit(now, 20, "DELETE", numRemovedFiles=1, numRemovedBytes=rules.GB)]
    f = rules.evaluate_storage(table(now, 10, 2000, history, props), now, cfg)
    assert f.remediation == ["VACUUM lakehouse.silver.user_events RETAIN 336 HOURS;"]


def test_both_actions(cfg, now):
    history = [commit(now, 9, "OPTIMIZE", numRemovedFiles=1, numRemovedBytes=rules.GB)]
    f = rules.evaluate_storage(table(now, 3000, 3000 * 10, history), now, cfg)
    assert f.recommended_action == "BOTH" and f.alert
    assert len(f.remediation) == 2


def test_retention_parsing():
    assert rules.retention_days({"delta.deletedFileRetentionDuration": "interval 168 hours"}, 7) == 7
    assert rules.retention_days({"delta.deletedFileRetentionDuration": "interval 2 weeks"}, 7) == 14
    assert rules.retention_days({}, 7) == 7
