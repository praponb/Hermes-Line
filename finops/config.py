"""Load config/auditor.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = Path(os.environ.get("FINOPS_CONFIG") or Path(__file__).resolve().parents[1] / "config" / "auditor.toml")


@dataclass(frozen=True)
class DatabricksTarget:
    profile: str
    warehouse_id: str = ""
    schemas: tuple[str, ...] = ()


@dataclass(frozen=True)
class GcpTarget:
    enabled: bool = False
    project: str = ""
    regions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Thresholds:
    idle_flag_hours: float = 2.0
    idle_alert_hours: float = 4.0
    oversized_driver_cores: int = 16
    business_hours_start: int = 8
    business_hours_end: int = 19
    off_hours_flag_hours: float = 1.0
    optimize_avg_file_mb: float = 128.0
    optimize_file_count: int = 1000
    optimize_min_files: int = 50
    alert_avg_file_mb: float = 32.0
    alert_file_count: int = 2000
    vacuum_retention_days: float = 7.0
    baseline_days: int = 7
    runaway_ratio: float = 1.5
    daily_waste_alert_usd: float = 100.0


@dataclass(frozen=True)
class Rates:
    default_node_hourly_usd: float = 1.0
    node_types: dict[str, float] = field(default_factory=dict)

    def node_hourly(self, node_type: str | None) -> float:
        return self.node_types.get(node_type or "", self.default_node_hourly_usd)


@dataclass(frozen=True)
class Config:
    environment_name: str
    timezone: str
    db_path: Path
    timeout_sec: int
    databricks: tuple[DatabricksTarget, ...]
    gcp: GcpTarget
    thresholds: Thresholds
    rates: Rates

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load(path: Path | str = DEFAULT_CONFIG) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    gcp = raw.get("gcp", {})
    rates = raw.get("rates", {})
    return Config(
        environment_name=raw.get("environment_name", "Lakehouse"),
        timezone=raw.get("timezone", "Asia/Bangkok"),
        db_path=Path(raw.get("db_path", "~/.hermes/audit/lakehouse_finops.db")).expanduser(),
        timeout_sec=int(raw.get("timeout_sec", 900)),
        databricks=tuple(
            DatabricksTarget(
                profile=t["profile"],
                warehouse_id=t.get("warehouse_id", ""),
                schemas=tuple(t.get("schemas", ())),
            )
            for t in raw.get("databricks", ())
        ),
        gcp=GcpTarget(
            enabled=bool(gcp.get("enabled", False)),
            project=gcp.get("project", ""),
            regions=tuple(gcp.get("regions", ())),
        ),
        thresholds=Thresholds(**raw.get("thresholds", {})),
        rates=Rates(
            default_node_hourly_usd=float(rates.get("default_node_hourly_usd", 1.0)),
            node_types={k: float(v) for k, v in rates.get("node_types", {}).items()},
        ),
    )


def shell_targets(cfg: Config) -> list[str]:
    """Tab-separated target lines consumed by the shell collectors."""
    lines = [
        "\t".join(["databricks", t.profile, t.warehouse_id or "-", " ".join(t.schemas) or "-"])
        for t in cfg.databricks
    ]
    if cfg.gcp.enabled:
        lines += ["\t".join(["dataproc", cfg.gcp.project, r]) for r in cfg.gcp.regions]
    return lines
