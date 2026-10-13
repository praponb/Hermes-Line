"""Plain-text triage brief for LINE (spec §7, adapted: LINE renders no markdown)."""

from __future__ import annotations

from dataclasses import dataclass, field

from finops.rules import (
    DATAPROC_NO_IDLE_TTL,
    IDLE_TIMEOUT,
    NO_AUTO_TERM,
    OFF_HOURS,
    OVERSIZED_DEV,
    ComputeFlag,
    StorageFlag,
)
from finops.trends import Trend

MAX_CHARS = 4500  # LINE text messages cap at 5,000 characters


@dataclass
class Note:
    level: str  # "warn" | "info"
    target: str
    message: str


@dataclass
class ReportInput:
    environment: str
    host: str
    run_id: str
    run_time_local: str
    clusters_checked: int
    tables_checked: int
    compute: list[ComputeFlag]
    storage: list[StorageFlag]
    trend: Trend
    notes: list[Note] = field(default_factory=list)


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _size(mb: float) -> str:
    return f"{mb * 1024:,.0f} KB" if mb < 1 else f"{mb:,.1f} MB"


def _compute_line(f: ComputeFlag) -> str:
    parts = [f.cluster_name if f.platform == "databricks" else f"{f.cluster_name} (Dataproc)"]
    if f.single_node:
        parts.append(f"Single-Node ({f.driver_cores} vCPU)" if f.driver_cores else "Single-Node")
    state = f.state
    if f.state in {"RUNNING", "RESIZING", "UPDATING"} and f.idle_hours:
        state += f" ({'idle' if f.idle_is_activity_based else 'up'} {f.idle_hours:.1f}h)"
    parts.append(state)
    labels = {
        NO_AUTO_TERM: "No auto-term",
        IDLE_TIMEOUT: "Idle timeout",
        OVERSIZED_DEV: "Oversized single-node",
        OFF_HOURS: f"Off-hours {f.off_hours_24h:.1f}h/24h",
        DATAPROC_NO_IDLE_TTL: "No idle-delete TTL",
    }
    parts.append(", ".join(labels[v] for v in f.violations))
    if f.est_daily_waste_usd:
        parts.append(f"Est. waste ~${f.est_daily_waste_usd:,.0f}/day")
    if f.alert and f.state == "RUNNING":
        parts.append("Action: terminate / set auto-term")
    return f"{'🔴' if f.alert else '•'} " + " | ".join(parts)


def _storage_lines(f: StorageFlag) -> list[str]:
    parts = [f.table]
    if f.recommended_action in {"OPTIMIZE", "BOTH"}:
        parts.append(f"{f.file_count:,} files (avg {_size(f.avg_file_size_mb)})")
    if f.recommended_action in {"VACUUM", "BOTH"}:
        stale = f"{f.stale_snapshots_count} unvacuumed commits past retention"
        if f.reclaimable_space_gb:
            stale += f" (~{f.reclaimable_space_gb:,.1f} GB)"
        parts.append(stale)
    lines = [f"{'🔴' if f.alert else '•'} " + " | ".join(parts)]
    lines += [f"   ↳ {sql}" for sql in f.remediation]
    return lines


def _trend_lines(r: ReportInput) -> list[str]:
    t = r.trend
    line = f"• Est. daily waste: {_usd(t.current_usd)}"
    if t.pct_change is not None:
        arrow = "▲" if t.pct_change > 0 else ("▼" if t.pct_change < 0 else "■")
        line += f" ({arrow} {t.pct_change:+.0f}% vs {t.baseline_runs}-run 7-day avg {_usd(t.baseline_usd)})"
    elif t.baseline_runs:
        line += f" (7-day avg {_usd(t.baseline_usd or 0)})"
    else:
        line += " (no 7-day baseline yet)"
    lines = [line]
    if t.runaway:
        lines.append(f"🔴 Cost runaway: above {t.runaway_ratio:.0%} of the 7-day baseline")
    if t.over_budget:
        lines.append(f"🔴 Daily waste above the {_usd(t.budget_usd)}/day alert threshold")
    lines.append(f"• Run ID: {r.run_id}")
    return lines


def _note_lines(r: ReportInput) -> list[str]:
    icon = {"warn": "⚠️", "info": "ℹ️"}
    return [f"{icon.get(n.level, '•')} {n.target}: {n.message}" for n in r.notes]


def has_alert(r: ReportInput) -> bool:
    return (
        any(f.alert for f in r.compute)
        or any(f.alert for f in r.storage)
        or r.trend.runaway
        or r.trend.over_budget
    )


def render(r: ReportInput) -> str:
    if not r.compute and not r.storage and not r.trend.runaway and not r.trend.over_budget:
        lines = [
            f"✅ Lakehouse FinOps audit: all clear ({r.environment}, "
            f"{r.clusters_checked} clusters / {r.tables_checked} tables checked, {r.run_id})"
        ]
        return "\n".join(lines + _note_lines(r))

    limit = max(len(r.compute), len(r.storage))
    while True:
        text = _render_full(r, limit)
        if len(text) <= MAX_CHARS or limit == 0:
            return text[:MAX_CHARS]
        limit -= 1


def _render_full(r: ReportInput, limit: int) -> str:
    icon = "🚨" if has_alert(r) else "⚠️"
    lines = [
        f"{icon} Lakehouse FinOps & Table Hygiene Audit",
        f"Host: {r.host} | {r.run_time_local} | Workspace: {r.environment}",
    ]
    if r.compute:
        lines += ["", f"Compute inefficiencies ({len(r.compute)} of {r.clusters_checked} clusters)"]
        ranked = sorted(r.compute, key=lambda f: (not f.alert, -f.est_daily_waste_usd))
        lines += [_compute_line(f) for f in ranked[:limit]]
        if len(ranked) > limit:
            lines.append(f"… +{len(ranked) - limit} more in DB")
    if r.storage:
        lines += ["", f"Storage hygiene ({len(r.storage)} of {r.tables_checked} tables)"]
        ranked = sorted(r.storage, key=lambda f: (not f.alert, -f.file_count, -f.reclaimable_space_gb))
        for f in ranked[:limit]:
            lines += _storage_lines(f)
        if len(ranked) > limit:
            lines.append(f"… +{len(ranked) - limit} more in DB")
    lines += ["", "FinOps trend"] + _trend_lines(r)
    notes = _note_lines(r)
    if notes:
        lines += [""] + notes
    return "\n".join(lines)
