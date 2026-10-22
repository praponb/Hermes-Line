"""Read-only web dashboard over the audit history DB.

Listens on localhost only; on the server a Cloudflare tunnel publishes it behind Cloudflare Access.
"""

from __future__ import annotations

import html
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from finops import db, trends
from finops.config import Config

DAYS = 30
# Set by Cloudflare Access on every request it lets through.
EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"


@dataclass
class Run:
    run_id: str
    ts_utc: datetime
    compute_flagged: int
    storage_flagged_gb: float
    waste_usd: float
    duration_sec: float


@dataclass
class ComputeRow:
    cluster_name: str
    violations: str
    hourly_rate_usd: float | None
    runtime_hours: float | None


@dataclass
class StorageRow:
    table: str
    file_count: int
    avg_file_size_mb: float
    stale_snapshots_count: int | None
    recommended_action: str
    reclaimable_space_gb: float | None


@dataclass
class Dashboard:
    runs: list[Run]  # oldest first
    compute: list[ComputeRow]  # findings of the latest run
    storage: list[StorageRow]
    trend: trends.Trend | None


EMPTY = Dashboard([], [], [], None)


def _utc(ts: str) -> datetime:
    return datetime.strptime(ts, trends.TS_FORMAT).replace(tzinfo=timezone.utc)


def load(conn: sqlite3.Connection, cfg: Config, now_utc: datetime, days: int = DAYS) -> Dashboard:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_runs'").fetchone():
        return EMPTY
    since = (now_utc - timedelta(days=days)).strftime(trends.TS_FORMAT)
    runs = [
        Run(run_id, _utc(ts), n, gb, waste, dur)
        for run_id, ts, n, gb, waste, dur in conn.execute(
            "SELECT run_id, timestamp, total_compute_flagged, total_storage_flagged_gb,"
            " estimated_daily_waste_usd, execution_duration_sec FROM audit_runs"
            " WHERE target_environment = ? AND timestamp >= ? ORDER BY timestamp, run_id",
            (cfg.environment_name, since),
        )
    ]
    if not runs:
        return EMPTY
    latest = runs[-1]
    compute = [
        ComputeRow(*row)
        for row in conn.execute(
            "SELECT cluster_name, GROUP_CONCAT(violation_type, ', '), MAX(hourly_rate_usd), MAX(runtime_hours)"
            " FROM compute_anomalies WHERE run_id = ? GROUP BY cluster_id, cluster_name"
            " ORDER BY MAX(hourly_rate_usd) DESC, cluster_name",
            (latest.run_id,),
        )
    ]
    storage = [
        StorageRow(*row)
        for row in conn.execute(
            "SELECT table_identifier, file_count, avg_file_size_mb, stale_snapshots_count, recommended_action,"
            " reclaimable_space_gb FROM storage_hygiene_flags WHERE run_id = ?"
            " ORDER BY file_count DESC, table_identifier",
            (latest.run_id,),
        )
    ]
    trend = trends.compute(conn, cfg, latest.run_id, latest.waste_usd, latest.ts_utc)
    return Dashboard(runs, compute, storage, trend)


def _usd(v: float | None) -> str:
    return "–" if v is None else f"${v:,.2f}"


def _num(v: float | None, fmt: str = ",.1f") -> str:
    return "–" if v is None else format(v, fmt)


def _local(cfg: Config, ts: datetime, fmt: str = "%a %d %b %H:%M") -> str:
    return ts.astimezone(cfg.tz).strftime(fmt)


def _waste_chart(cfg: Config, runs: list[Run]) -> str:
    w, h, left, right, top_pad, bottom = 720, 220, 56, 12, 14, 30
    plot_w, plot_h = w - left - right, h - top_pad - bottom
    budget = cfg.thresholds.daily_waste_alert_usd
    peak = max([r.waste_usd for r in runs] + [1.0])
    show_budget = budget <= peak * 1.5
    if show_budget:
        peak = max(peak, budget)
    scale = peak * 1.1

    def y(v: float) -> float:
        return top_pad + plot_h * (1 - v / scale)

    parts = []
    for v in (0, peak / 2, peak):
        parts.append(
            f'<line class="grid" x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">${v:,.0f}</text>'
        )
    step = plot_w / len(runs)
    bar_w = max(min(step * 0.7, 28), 2)
    label_every = max(1, math.ceil(len(runs) / 7))
    for i, r in enumerate(runs):
        x = left + step * i + (step - bar_w) / 2
        bar_h = max(plot_h * r.waste_usd / scale, 1)
        cls = "bar alert" if r.waste_usd > budget else "bar"
        tip = f"{_local(cfg, r.ts_utc)} · {_usd(r.waste_usd)}/day · {r.compute_flagged} clusters flagged"
        parts.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{y(0) - bar_h:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}"'
            f' rx="2"><title>{html.escape(tip)}</title></rect>'
        )
        if i % label_every == 0:
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{h - 10}" text-anchor="middle">'
                f"{_local(cfg, r.ts_utc, '%d %b')}</text>"
            )
    if show_budget:
        parts.append(
            f'<line class="budget" x1="{left}" x2="{w - right}" y1="{y(budget):.1f}" y2="{y(budget):.1f}"/>'
            f'<text class="tick budget-label" x="{w - right}" y="{y(budget) - 5:.1f}" text-anchor="end">'
            f"alert {_usd(budget)}/day</text>"
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Estimated daily waste per run">'
        + "".join(parts)
        + "</svg>"
    )


def _tile(label: str, value: str, sub: str = "", alert: bool = False) -> str:
    cls = "tile alert" if alert else "tile"
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    return (
        f'<div class="{cls}"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div>{sub_html}</div>'
    )


def _table(headers: list[str], rows: list[list[str]], numeric: set[int], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{html.escape(empty)}</p>'
    th = "".join(f'<th class="{"num" if i in numeric else ""}">{html.escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>"
        + "".join(f'<td class="{"num" if i in numeric else ""}">{html.escape(c)}</td>' for i, c in enumerate(row))
        + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def render(cfg: Config, d: Dashboard, user: str | None, now_utc: datetime) -> str:
    env = html.escape(cfg.environment_name)
    who = f" · signed in as {html.escape(user)}" if user else ""
    if not d.runs:
        main = f'<p class="empty">No audit runs in the last {DAYS} days yet.</p>'
    else:
        latest, t = d.runs[-1], d.trend
        if t.pct_change is not None:
            vs = f"{t.pct_change:+.0f}% vs {t.baseline_runs}-run avg {_usd(t.baseline_usd)}"
        else:
            vs = "no 7-day baseline yet"
        tiles = "".join(
            [
                _tile("Est. daily waste", _usd(latest.waste_usd), vs, t.runaway or t.over_budget),
                _tile("Clusters flagged", str(latest.compute_flagged), "latest run"),
                _tile("Storage flagged", f"{latest.storage_flagged_gb:,.1f} GB", f"{len(d.storage)} tables"),
                _tile("Runs", str(len(d.runs)), f"last {DAYS} days"),
            ]
        )
        compute = _table(
            ["Cluster", "Violations", "Rate/hour", "Runtime (h)"],
            [[c.cluster_name, c.violations, _usd(c.hourly_rate_usd), _num(c.runtime_hours)] for c in d.compute],
            {2, 3},
            "No compute findings in the latest run.",
        )
        storage = _table(
            ["Table", "Action", "Files", "Avg file (MB)", "Stale commits", "Reclaimable (GB)"],
            [
                [
                    s.table,
                    s.recommended_action,
                    f"{s.file_count:,}",
                    _num(s.avg_file_size_mb),
                    _num(s.stale_snapshots_count, ","),
                    _num(s.reclaimable_space_gb),
                ]
                for s in d.storage
            ],
            {2, 3, 4, 5},
            "No storage findings in the latest run.",
        )
        runs = _table(
            ["Run (Bangkok time)", "Run ID", "Clusters", "Storage (GB)", "Waste/day", "Duration (s)"],
            [
                [
                    _local(cfg, r.ts_utc),
                    r.run_id,
                    str(r.compute_flagged),
                    _num(r.storage_flagged_gb),
                    _usd(r.waste_usd),
                    _num(r.duration_sec),
                ]
                for r in reversed(d.runs)
            ],
            {2, 3, 4, 5},
            "",
        )
        main = f"""
<p class="meta">Latest run {html.escape(_local(cfg, latest.ts_utc))} Bangkok time · {html.escape(latest.run_id)}</p>
<section class="tiles">{tiles}</section>
<section class="card"><h2>Estimated daily waste per run</h2>{_waste_chart(cfg, d.runs)}</section>
<section class="card"><h2>Compute findings · latest run</h2>{compute}</section>
<section class="card"><h2>Storage hygiene · latest run</h2>{storage}</section>
<section class="card"><h2>Runs · last {DAYS} days</h2>{runs}</section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lakehouse FinOps</title>
<style>{CSS}</style>
</head>
<body>
<header><h1>Lakehouse FinOps</h1>
<p class="meta">{env} · updated {html.escape(_local(cfg, now_utc))} Bangkok time{who}</p></header>
<main>{main}</main>
<footer>Read-only view of the auditor's history DB. Remediation SQL is in the LINE report.</footer>
</body>
</html>
"""


CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --text: #1c2330; --muted: #5d6778; --line: #e3e6eb;
  --bar: #3b6fd8; --alert: #d0453a; --alert-bg: #fdeceb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151b; --card: #1b1f27; --text: #e6e9ef; --muted: #9aa3b2; --line: #2c323d;
    --bar: #6b95ee; --alert: #f07167; --alert-bg: #3a1f1f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
header, main, footer { max-width: 1040px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 24px; }
h1 { font-size: 24px; margin: 0; }
h2 { font-size: 15px; margin: 0 0 12px; }
.meta, footer, .sub, .label, .empty { color: var(--muted); }
.meta { margin: 4px 0 16px; font-size: 13px; }
footer { font-size: 12px; padding-top: 8px; padding-bottom: 32px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 12px; }
.tile, .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.card { margin-bottom: 12px; }
.tile.alert { background: var(--alert-bg); border-color: var(--alert); }
.label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.value { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile.alert .value { color: var(--alert); }
.sub { font-size: 12px; }
svg { width: 100%; height: auto; display: block; }
.grid { stroke: var(--line); }
.tick { fill: var(--muted); font-size: 11px; }
.bar { fill: var(--bar); }
.bar.alert { fill: var(--alert); }
.budget { stroke: var(--alert); stroke-dasharray: 4 4; }
.budget-label { fill: var(--alert); }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 500; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.empty { margin: 0; }
"""

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
}


def page(cfg: Config, db_path: Path, user: str | None = None, now_utc: datetime | None = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        conn = db.connect_readonly(db_path)
    except sqlite3.OperationalError:  # no DB until the first audit run
        return render(cfg, EMPTY, user, now_utc)
    try:
        return render(cfg, load(conn, cfg, now_utc), user, now_utc)
    finally:
        conn.close()


def make_server(cfg: Config, db_path: Path, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._send(200, "text/plain", "ok\n")
            elif path == "/":
                try:
                    body = page(cfg, db_path, self.headers.get(EMAIL_HEADER))
                except Exception as e:  # keep serving; the traceback goes to the container log
                    self.log_error("render failed: %r", e)
                    self._send(500, "text/plain", "dashboard error, see the container log\n")
                else:
                    self._send(200, "text/html", body)
            else:
                self._send(404, "text/plain", "not found\n")

        def _send(self, status: int, content_type: str, body: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer((host, port), Handler)


def serve(cfg: Config, db_path: Path, host: str, port: int) -> None:
    server = make_server(cfg, db_path, host, port)
    print(f"finops web on http://{host}:{server.server_port} (db {db_path})", file=sys.stderr, flush=True)
    server.serve_forever()
