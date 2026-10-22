"""CLI: `python3 -m finops targets` (for the shell collectors) and `python3 -m finops run`."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from finops import config as config_mod
from finops import db, report, rules, trends, web


@dataclass
class RunRecord:
    run_id: str
    timestamp_utc: str
    target_environment: str
    total_compute_flagged: int
    total_storage_flagged_gb: float
    estimated_daily_waste_usd: float
    execution_duration_sec: float


def host_label() -> str:
    try:
        chip = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        chip = ""
    return chip or platform.machine()


def make_run_id(now_local: datetime) -> str:
    return f"run_{now_local:%Y%m%d_%H%M}_{platform.machine()}"


def load_run_dir(run_dir: Path) -> tuple[list[dict], list[dict], list[report.Note], int]:
    """Read collector outputs; returns (compute records, storage records, notes, successful targets)."""
    compute, storage, ok = [], [], 0
    for path in sorted(run_dir.glob("compute_*.json")):
        compute += json.loads(path.read_text() or "[]")
        ok += 1
    for path in sorted(run_dir.glob("storage_*.json")):
        storage += json.loads(path.read_text() or "[]")
        ok += 1
    notes = []
    errors = run_dir / "errors.jsonl"
    if errors.exists():
        for line in errors.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                notes.append(report.Note(e.get("level", "warn"), e.get("target", "?"), e.get("message", "")))
    return compute, storage, notes, ok


def cmd_targets(args) -> int:
    for line in config_mod.shell_targets(config_mod.load(args.config)):
        print(line)
    return 0


def cmd_get(args) -> int:
    print(getattr(config_mod.load(args.config), args.key))
    return 0


def cmd_run(args) -> int:
    cfg = config_mod.load(args.config)
    run_dir = Path(args.run_dir)
    compute_records, storage_records, notes, ok = load_run_dir(run_dir)
    if ok == 0:
        print("FinOps audit failed: no target could be collected.", file=sys.stderr)
        for n in notes:
            print(f"- {n.target}: {n.message}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    now_local = now.astimezone(cfg.tz)
    run_id = args.run_id or make_run_id(now_local)

    compute_flags = [f for r in compute_records if (f := rules.evaluate_compute(r, now, cfg))]
    storage_flags = [f for r in storage_records if (f := rules.evaluate_storage(r, now, cfg))]
    waste = sum(f.est_daily_waste_usd for f in compute_flags)

    conn = db.connect(Path(args.db).expanduser() if args.db else cfg.db_path)
    run_id = db.unique_run_id(conn, run_id)
    trend = trends.compute(conn, cfg, run_id, waste, now)
    started = args.started_at or time.time()
    db.save_run(
        conn,
        RunRecord(
            run_id=run_id,
            timestamp_utc=now.strftime(trends.TS_FORMAT),
            target_environment=cfg.environment_name,
            total_compute_flagged=len(compute_flags),
            total_storage_flagged_gb=round(sum(f.size_gb for f in storage_flags), 3),
            estimated_daily_waste_usd=round(waste, 2),
            execution_duration_sec=round(time.time() - started, 1),
        ),
        compute_flags,
        storage_flags,
    )
    conn.close()

    print(
        report.render(
            report.ReportInput(
                environment=cfg.environment_name,
                host=host_label(),
                run_id=run_id,
                run_time_local=f"{now_local:%a %d %b %H:%M} {cfg.timezone.split('/')[-1]} time",
                clusters_checked=len(compute_records),
                tables_checked=len(storage_records),
                compute=compute_flags,
                storage=storage_flags,
                trend=trend,
                notes=notes,
            )
        )
    )
    return 0


def cmd_web(args) -> int:
    cfg = config_mod.load(args.config)
    web.serve(cfg, Path(args.db).expanduser() if args.db else cfg.db_path, args.host, args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finops")
    parser.add_argument("--config", default=str(config_mod.DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("targets", help="print collector targets as TSV").set_defaults(func=cmd_targets)
    get = sub.add_parser("get", help="print a top-level config value")
    get.add_argument("key", choices=["environment_name", "timezone", "db_path", "timeout_sec"])
    get.set_defaults(func=cmd_get)
    run = sub.add_parser("run", help="evaluate collected JSON, persist, print report")
    run.add_argument("--run-dir", required=True)
    run.add_argument("--db", help="override db_path from config")
    run.add_argument("--run-id")
    run.add_argument("--started-at", type=float, help="epoch seconds when the run started")
    run.set_defaults(func=cmd_run)
    web_p = sub.add_parser("web", help="serve the read-only history dashboard")
    web_p.add_argument("--host", default="127.0.0.1")
    web_p.add_argument("--port", type=int, default=8650)
    web_p.add_argument("--db", help="override db_path from config")
    web_p.set_defaults(func=cmd_web)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
