# Hermes-Line: Lakehouse FinOps & Table Hygiene Auditor

A read-only auditor that inspects Databricks (and optionally GCP Dataproc)
compute and Delta table hygiene, stores each run in SQLite, and prints a short
triage report. The Hermes agent runtime runs it on a schedule and delivers the
report to LINE.

```text
Hermes cron (weekdays 08:00 Bangkok)
  └─ ~/.hermes/scripts/lakehouse_finops.sh  (generated wrapper, pins PATH)
       └─ scripts/run_audit.sh
            ├─ preflight.sh       check databricks / gcloud credentials
            ├─ audit_compute.sh   Module A: list clusters → JSON
            ├─ audit_storage.sh   Module B: DESCRIBE DETAIL / HISTORY
            └─ python3 -m finops run
                 rules → SQLite history → trend → report on stdout
Hermes delivers stdout to LINE verbatim; a non-zero exit becomes a
failure alert.
```

## What it checks

### Compute (Module A)

| Rule | Meaning |
| --- | --- |
| `NO_AUTO_TERM` | Interactive cluster with auto-termination off |
| `IDLE_TIMEOUT` | Running and idle longer than `idle_flag_hours` |
| `OVERSIZED_DEV` | Single node with ≥ `oversized_driver_cores` driver cores |
| `OFF_HOURS` | Ran ≥ `off_hours_flag_hours` outside business hours (24 h) |
| `DATAPROC_NO_IDLE_TTL` | Dataproc cluster without an idle-delete TTL |

A flag becomes an alert when a running cluster has no auto-termination or idle
TTL, or has been idle longer than `idle_alert_hours`. Job and pipeline clusters
are exempt from the interactive-only rules. Estimated daily waste uses the
per-node-hour rates in `[rates]`.

### Storage (Module B)

Flags Delta tables that need `OPTIMIZE` (small files / too many files) or
`VACUUM` (file-removing commits older than the retention period and not yet
vacuumed), and prints the remediation SQL. The auditor never runs these itself.

### Trend

Compares today's estimated waste against the average over `baseline_days`,
alerting on a runaway ratio or when waste exceeds `daily_waste_alert_usd`.

## Requirements

- Python ≥ 3.11 (standard library only at runtime)
- [uv](https://docs.astral.sh/uv/) for tests
- Databricks CLI v0.200+ (Go binary), `jq`, `perl` (for timeouts on macOS)
- `gcloud` with Application Default Credentials, if `[gcp].enabled = true`
- A `~/.databrickscfg` profile (default `lakehouse-audit`) and a SQL warehouse
  for the storage audit

No credentials live in this repo. Databricks uses `~/.databrickscfg` profiles;
GCP uses gcloud ADC.

## Configuration

Everything is in `config/auditor.toml`: environment name, timezone, DB path,
the 15-minute run limit, Databricks profiles/warehouse/schemas, GCP
project/regions, thresholds and cost rates. Set
`FINOPS_CONFIG=/path/to/other.toml` to use a different file.

## Commands

### Setup

```bash
# Create .venv with dev dependencies (pytest)
uv sync
# (Re)generate ~/.hermes/scripts/lakehouse_finops.sh
scripts/install_hermes.sh
# Confirm the Databricks profile works
databricks auth describe -p lakehouse-audit
# Set up GCP ADC (only if GCP is enabled)
gcloud auth application-default login
```

Rerun `install_hermes.sh` whenever you move the repo or reinstall `python3`,
`databricks`, `gcloud`, `jq` or `perl`, because the wrapper pins their
directories.

### Tests

```bash
uv run pytest                          # full suite
uv run pytest tests/test_rules.py -v   # just the rule tests
```

### Run the audit manually

```bash
# Full audit, saved to /tmp/finops/dry-run.db instead of the real DB
scripts/run_audit.sh --dry-run
# Same, with collector progress on stderr
FINOPS_VERBOSE=1 scripts/run_audit.sh --dry-run
# Full audit, saved to the real history DB
scripts/run_audit.sh
```

Both print the report to the terminal only; nothing is sent to LINE. They do
query Databricks (read-only) and wake the SQL warehouse. A non-dry run is
included in the 7-day trend baseline.

### `finops` CLI

```bash
# Collector targets as TSV
python3 -m finops targets
# One config value: db_path | environment_name | timezone | timeout_sec
python3 -m finops get db_path
# Evaluate collected JSON, save it and print the report
python3 -m finops run --run-dir DIR \
  [--db PATH] [--run-id ID] [--started-at EPOCH]
# Use a different config file
python3 -m finops --config other.toml targets
```

### Individual collectors (debugging)

The collectors need `RUN_DIR` set:

```bash
export RUN_DIR=$(mktemp -d)
scripts/preflight.sh databricks lakehouse-audit
scripts/preflight.sh gcloud
scripts/audit_compute.sh databricks lakehouse-audit
scripts/audit_compute.sh dataproc praponb-lakehouse asia-southeast1
scripts/audit_storage.sh lakehouse-audit b8da7ee15120a028 \
  "lakehouse.bronze lakehouse.silver lakehouse.gold"
python3 -m finops run --run-dir "$RUN_DIR" --db /tmp/finops/debug.db
# Notes recorded by the collectors
cat "$RUN_DIR/errors.jsonl"
```

`CLI_TIMEOUT` (default 120 s) limits each CLI call.

### Hermes schedule

The job is `lakehouse-finops-auditor`, id `27f143f1b1ae`.

```bash
hermes cron list                          # show jobs and next run
hermes cron status                        # is the scheduler running?
hermes cron runs 27f143f1b1ae --limit 10  # recent executions
hermes cron run 27f143f1b1ae              # run now (sends a real LINE message)
hermes cron pause 27f143f1b1ae
hermes cron resume 27f143f1b1ae
hermes cron doctor                        # health check for scheduled jobs
ls ~/.hermes/cron/output/27f143f1b1ae/    # saved reports from past runs
```

`hermes cron run` starts the job on the next scheduler tick.

The job was created as a no-agent script job, roughly:

```bash
hermes cron create '0 8 * * 1-5' --name lakehouse-finops-auditor \
  --script lakehouse_finops.sh --no-agent --deliver line:<LINE_USER_ID>
```

### Query the history DB

```bash
DB=~/.hermes/audit/lakehouse_finops.db
sqlite3 -header -column "$DB" \
  "SELECT * FROM audit_runs ORDER BY timestamp DESC LIMIT 10;"
sqlite3 -header -column "$DB" \
  "SELECT run_id, cluster_name, violation_type, hourly_rate_usd
   FROM compute_anomalies ORDER BY id DESC LIMIT 20;"
sqlite3 -header -column "$DB" \
  "SELECT run_id, table_identifier, recommended_action, reclaimable_space_gb
   FROM storage_hygiene_flags ORDER BY id DESC LIMIT 20;"
```

Timestamps in `audit_runs` are UTC.

## Project layout

```text
config/auditor.toml        settings, thresholds, cost rates
finops/__main__.py         CLI (targets | get | run)
finops/config.py           TOML loader
finops/rules.py            compute and storage rules
finops/trends.py           rolling-baseline waste comparison
finops/report.py           LINE-sized report rendering
finops/db.py               SQLite schema and persistence
scripts/run_audit.sh       entrypoint: preflight → collect → evaluate → report
scripts/preflight.sh       credential checks
scripts/audit_compute.sh   Module A collector
scripts/audit_storage.sh   Module B collector
scripts/lib.sh             shared shell helpers (timeouts, SQL statement API)
scripts/install_hermes.sh  installs the Hermes wrapper script
tests/                     pytest suite
spec.docx                  original specification
```
