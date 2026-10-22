"""SQLite persistence (spec §5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- Audit execution run records
CREATE TABLE IF NOT EXISTS audit_runs (
    run_id TEXT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    target_environment TEXT NOT NULL,
    total_compute_flagged INTEGER NOT NULL,
    total_storage_flagged_gb REAL NOT NULL,
    estimated_daily_waste_usd REAL NOT NULL,
    execution_duration_sec REAL NOT NULL
);

-- Historical snapshot of flagged compute resources
CREATE TABLE IF NOT EXISTS compute_anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    cluster_id TEXT NOT NULL,
    cluster_name TEXT NOT NULL,
    violation_type TEXT NOT NULL, -- e.g., 'NO_AUTO_TERM', 'IDLE_TIMEOUT', 'OVERSIZED_DEV'
    hourly_rate_usd REAL,
    runtime_hours REAL,
    FOREIGN KEY(run_id) REFERENCES audit_runs(run_id)
);

-- Historical snapshot of flagged tables and hygiene issues
CREATE TABLE IF NOT EXISTS storage_hygiene_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    table_identifier TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    avg_file_size_mb REAL NOT NULL,
    stale_snapshots_count INTEGER,
    recommended_action TEXT NOT NULL, -- 'OPTIMIZE', 'VACUUM', 'BOTH'
    reclaimable_space_gb REAL,
    FOREIGN KEY(run_id) REFERENCES audit_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_runs_env_ts ON audit_runs(target_environment, timestamp);
CREATE INDEX IF NOT EXISTS idx_compute_anomalies_run ON compute_anomalies(run_id);
CREATE INDEX IF NOT EXISTS idx_storage_flags_run ON storage_hygiene_flags(run_id);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    """Open an existing DB without creating or migrating it (the web dashboard mounts it read-only)."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def unique_run_id(conn: sqlite3.Connection, run_id: str) -> str:
    """Suffix the run id when a run already exists for the same minute (e.g. a manual rerun)."""
    candidate, n = run_id, 1
    while conn.execute("SELECT 1 FROM audit_runs WHERE run_id = ?", (candidate,)).fetchone():
        n += 1
        candidate = f"{run_id}_{n}"
    return candidate


def save_run(conn: sqlite3.Connection, run, compute_flags, storage_flags) -> None:
    """Persist one audit run and its findings in a single transaction."""
    with conn:
        conn.execute(
            "INSERT INTO audit_runs (run_id, timestamp, target_environment, total_compute_flagged,"
            " total_storage_flagged_gb, estimated_daily_waste_usd, execution_duration_sec)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.timestamp_utc,
                run.target_environment,
                run.total_compute_flagged,
                run.total_storage_flagged_gb,
                run.estimated_daily_waste_usd,
                run.execution_duration_sec,
            ),
        )
        conn.executemany(
            "INSERT INTO compute_anomalies (run_id, cluster_id, cluster_name, violation_type,"
            " hourly_rate_usd, runtime_hours) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run.run_id, f.cluster_id, f.cluster_name, v, f.hourly_rate_usd, f.runtime_hours)
                for f in compute_flags
                for v in f.violations
            ],
        )
        conn.executemany(
            "INSERT INTO storage_hygiene_flags (run_id, table_identifier, file_count, avg_file_size_mb,"
            " stale_snapshots_count, recommended_action, reclaimable_space_gb)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run.run_id,
                    f.table,
                    f.file_count,
                    f.avg_file_size_mb,
                    f.stale_snapshots_count,
                    f.recommended_action,
                    f.reclaimable_space_gb,
                )
                for f in storage_flags
            ],
        )
