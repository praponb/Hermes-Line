"""Rolling-baseline comparison of estimated daily waste (spec §6)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from finops.config import Config

TS_FORMAT = "%Y-%m-%d %H:%M:%S"  # matches SQLite CURRENT_TIMESTAMP (UTC)


@dataclass
class Trend:
    current_usd: float
    baseline_usd: float | None
    baseline_runs: int
    pct_change: float | None
    runaway: bool
    over_budget: bool
    runaway_ratio: float = 1.5
    budget_usd: float = 100.0


def compute(conn: sqlite3.Connection, cfg: Config, run_id: str, current_usd: float, now_utc: datetime) -> Trend:
    t = cfg.thresholds
    since = (now_utc - timedelta(days=t.baseline_days)).strftime(TS_FORMAT)
    avg, n = conn.execute(
        "SELECT AVG(estimated_daily_waste_usd), COUNT(*) FROM audit_runs"
        " WHERE target_environment = ? AND timestamp >= ? AND run_id != ?",
        (cfg.environment_name, since, run_id),
    ).fetchone()
    pct = None
    if avg:
        pct = (current_usd - avg) / avg * 100
    return Trend(
        current_usd=round(current_usd, 2),
        baseline_usd=None if avg is None else round(avg, 2),
        baseline_runs=n,
        pct_change=None if pct is None else round(pct, 1),
        runaway=bool(avg) and current_usd > t.runaway_ratio * avg,
        over_budget=current_usd > t.daily_waste_alert_usd,
        runaway_ratio=t.runaway_ratio,
        budget_usd=t.daily_waste_alert_usd,
    )
