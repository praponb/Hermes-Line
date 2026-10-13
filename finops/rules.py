"""Compute (spec §4 Module A) and storage (Module B) audit rules.

Inputs are the normalized JSON records written by scripts/audit_compute.sh and
scripts/audit_storage.sh. All rules are pure functions of (record, now, config).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from finops.config import Config

MB = 1024 * 1024
GB = 1024 * MB

NO_AUTO_TERM = "NO_AUTO_TERM"
IDLE_TIMEOUT = "IDLE_TIMEOUT"
OVERSIZED_DEV = "OVERSIZED_DEV"
OFF_HOURS = "OFF_HOURS"
DATAPROC_NO_IDLE_TTL = "DATAPROC_NO_IDLE_TTL"

RUNNING_STATES = {"RUNNING", "RESIZING", "UPDATING"}


@dataclass
class ComputeFlag:
    platform: str
    cluster_id: str
    cluster_name: str
    state: str
    violations: list[str]
    alert: bool
    hourly_rate_usd: float
    runtime_hours: float
    idle_hours: float
    idle_is_activity_based: bool
    off_hours_24h: float
    driver_cores: int | None
    single_node: bool
    est_daily_waste_usd: float


@dataclass
class StorageFlag:
    table: str
    file_count: int
    size_gb: float
    avg_file_size_mb: float
    stale_snapshots_count: int
    reclaimable_space_gb: float
    recommended_action: str
    alert: bool
    remediation: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _ms_to_dt(ms) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def _iso_to_dt(s) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(delta: timedelta) -> float:
    return max(delta.total_seconds() / 3600, 0.0)


def off_hours_overlap(start: datetime, now: datetime, cfg: Config, window_hours: float = 24) -> float:
    """Hours of [start, now] (clipped to the last `window_hours`) outside local business hours."""
    t = cfg.thresholds
    begin = max(start, now - timedelta(hours=window_hours))
    step = timedelta(minutes=5)
    total = timedelta()
    cur = begin
    while cur < now:
        nxt = min(cur + step, now)
        local_hour = cur.astimezone(cfg.tz).hour
        if not (t.business_hours_start <= local_hour < t.business_hours_end):
            total += nxt - cur
        cur = nxt
    return _hours(total)


def machine_cores(machine_type: str | None) -> int | None:
    """vCPUs from a GCE machine type such as 'n2-highmem-16' (custom types: 'n2-custom-16-65536')."""
    if not machine_type:
        return None
    m = re.search(r"-(?:custom-)?(\d+)(?:-\d+)?$", machine_type)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- compute


def evaluate_compute(record: dict, now: datetime, cfg: Config) -> ComputeFlag | None:
    if record.get("platform") == "dataproc":
        return _evaluate_dataproc(record, now, cfg)
    return _evaluate_databricks(record, now, cfg)


def _evaluate_databricks(r: dict, now: datetime, cfg: Config) -> ComputeFlag | None:
    t = cfg.thresholds
    state = r.get("state") or "UNKNOWN"
    running = state in RUNNING_STATES
    # Job clusters terminate with their run; autotermination_minutes == 0 is normal for them.
    interactive = (r.get("cluster_source") or "UI") not in {"JOB", "PIPELINE", "PIPELINE_MAINTENANCE"}

    started = _ms_to_dt(r.get("last_restarted_time")) or _ms_to_dt(r.get("start_time"))
    last_activity = _ms_to_dt(r.get("last_activity_time"))
    runtime_hours = _hours(now - started) if running and started else 0.0
    idle_since = last_activity or started
    idle_hours = _hours(now - idle_since) if running and idle_since else 0.0

    workers = r.get("num_workers") or 0
    autoscale = r.get("autoscale_min") is not None
    if autoscale:
        workers = max(workers, r.get("autoscale_min") or 0)
    single_node = workers == 0 and not autoscale
    driver_cores = r.get("driver_cores")

    violations: list[str] = []
    alert = False
    if interactive and (r.get("autotermination_minutes") or 0) == 0:
        violations.append(NO_AUTO_TERM)
        alert |= running
    if interactive and running and idle_hours > t.idle_flag_hours:
        violations.append(IDLE_TIMEOUT)
        alert |= idle_hours > t.idle_alert_hours
    if single_node and driver_cores is not None and driver_cores >= t.oversized_driver_cores:
        violations.append(OVERSIZED_DEV)
    off_hours = off_hours_overlap(started, now, cfg) if running and started else 0.0
    if interactive and running and off_hours >= t.off_hours_flag_hours:
        violations.append(OFF_HOURS)
    if not violations:
        return None

    rates = cfg.rates
    hourly = rates.node_hourly(r.get("driver_node_type_id") or r.get("node_type_id")) + workers * rates.node_hourly(
        r.get("node_type_id")
    )
    return _flag(
        "databricks", r, state, violations, alert, hourly, runtime_hours, idle_hours,
        last_activity is not None, off_hours, driver_cores, single_node, running, cfg,
    )


def _evaluate_dataproc(r: dict, now: datetime, cfg: Config) -> ComputeFlag | None:
    t = cfg.thresholds
    state = r.get("state") or "UNKNOWN"
    running = state in RUNNING_STATES
    started = _iso_to_dt(r.get("state_start_time"))
    runtime_hours = _hours(now - started) if running and started else 0.0
    workers = r.get("num_workers") or 0
    single_node = workers == 0
    driver_cores = machine_cores(r.get("master_machine_type"))

    violations: list[str] = []
    alert = False
    if not r.get("idle_delete_ttl"):
        violations.append(DATAPROC_NO_IDLE_TTL)
        alert |= running
    # Dataproc exposes no activity timestamp; uptime is the idle proxy.
    if running and runtime_hours > t.idle_flag_hours and not r.get("idle_delete_ttl"):
        violations.append(IDLE_TIMEOUT)
        alert |= runtime_hours > t.idle_alert_hours
    if single_node and driver_cores is not None and driver_cores >= t.oversized_driver_cores:
        violations.append(OVERSIZED_DEV)
    off_hours = off_hours_overlap(started, now, cfg) if running and started else 0.0
    if running and off_hours >= t.off_hours_flag_hours:
        violations.append(OFF_HOURS)
    if not violations:
        return None

    rates = cfg.rates
    hourly = rates.node_hourly(r.get("master_machine_type")) + workers * rates.node_hourly(
        r.get("worker_machine_type")
    )
    return _flag(
        "dataproc", r, state, violations, alert, hourly, runtime_hours, runtime_hours,
        False, off_hours, driver_cores, single_node, running, cfg,
    )


def _flag(platform, r, state, violations, alert, hourly, runtime_hours, idle_hours, activity_based,
          off_hours, driver_cores, single_node, running, cfg: Config) -> ComputeFlag:
    wasted = 0.0
    if running:
        idle_part = idle_hours if idle_hours > cfg.thresholds.idle_flag_hours else 0.0
        wasted = min(24.0, max(idle_part, off_hours))
    return ComputeFlag(
        platform=platform,
        cluster_id=r.get("cluster_id") or "",
        cluster_name=r.get("cluster_name") or r.get("cluster_id") or "?",
        state=state,
        violations=violations,
        alert=alert,
        hourly_rate_usd=round(hourly, 4),
        runtime_hours=round(runtime_hours, 2),
        idle_hours=round(idle_hours, 2),
        idle_is_activity_based=activity_based,
        off_hours_24h=round(off_hours, 2),
        driver_cores=driver_cores,
        single_node=single_node,
        est_daily_waste_usd=round(hourly * wasted, 2),
    )


# ---------------------------------------------------------------- storage

_RETENTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(week|day|hour|minute)s?", re.I)
_RETENTION_UNIT_DAYS = {"week": 7, "day": 1, "hour": 1 / 24, "minute": 1 / 1440}
_REMOVED_BYTES = ("numRemovedBytes", "numTargetBytesRemoved")
_REMOVED_FILES = ("numRemovedFiles", "numTargetFilesRemoved")


def _parse_json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def retention_days(properties: dict, default: float) -> float:
    m = _RETENTION_RE.search(str(properties.get("delta.deletedFileRetentionDuration", "")))
    return float(m.group(1)) * _RETENTION_UNIT_DAYS[m.group(2).lower()] if m else default


def stale_snapshots(history: list[dict], now: datetime, retention: float) -> tuple[int, int]:
    """(commits that removed files, are older than retention and not yet vacuumed; reclaimable bytes)."""
    vacuums = [_iso_to_dt(h.get("timestamp")) for h in history if str(h.get("operation", "")).startswith("VACUUM")]
    last_vacuum = max((v for v in vacuums if v), default=None)
    cutoff = now - timedelta(days=retention)
    count, reclaimable = 0, 0
    for h in history:
        ts = _iso_to_dt(h.get("timestamp"))
        if ts is None or ts >= cutoff or (last_vacuum and ts <= last_vacuum):
            continue
        metrics = _parse_json(h.get("operationMetrics"), {})
        removed_bytes = sum(int(metrics.get(k) or 0) for k in _REMOVED_BYTES)
        removed_files = sum(int(metrics.get(k) or 0) for k in _REMOVED_FILES)
        if removed_bytes or removed_files:
            count += 1
            reclaimable += removed_bytes
    return count, reclaimable


def evaluate_storage(record: dict, now: datetime, cfg: Config) -> StorageFlag | None:
    t = cfg.thresholds
    detail = record.get("detail") or {}
    table = record.get("table") or detail.get("name") or "?"
    file_count = int(detail.get("numFiles") or 0)
    size_bytes = int(detail.get("sizeInBytes") or 0)
    avg_mb = (size_bytes / file_count / MB) if file_count else 0.0

    needs_optimize = file_count >= t.optimize_min_files and (
        avg_mb < t.optimize_avg_file_mb or file_count > t.optimize_file_count
    )
    alert = file_count > t.alert_file_count and avg_mb < t.alert_avg_file_mb

    properties = _parse_json(detail.get("properties"), {})
    retention = retention_days(properties, t.vacuum_retention_days)
    stale_count, reclaimable_bytes = stale_snapshots(record.get("history") or [], now, retention)
    needs_vacuum = stale_count > 0

    if not (needs_optimize or needs_vacuum):
        return None
    action = "BOTH" if needs_optimize and needs_vacuum else ("OPTIMIZE" if needs_optimize else "VACUUM")
    remediation = []
    if needs_optimize:
        remediation.append(f"OPTIMIZE {table};")
    if needs_vacuum:
        remediation.append(f"VACUUM {table} RETAIN {round(retention * 24)} HOURS;")
    return StorageFlag(
        table=table,
        file_count=file_count,
        size_gb=round(size_bytes / GB, 3),
        avg_file_size_mb=round(avg_mb, 2),
        stale_snapshots_count=stale_count,
        reclaimable_space_gb=round(reclaimable_bytes / GB, 3),
        recommended_action=action,
        alert=alert,
        remediation=remediation,
    )
