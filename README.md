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

## Where it runs

Since 2026-10-13, production runs in Docker on the Ubuntu server
`192.168.1.49` (`ssh prapon@192.168.1.49`, stack in `~/Hermes-Line/deploy`).
See [Deploy with Docker](#deploy-with-docker-ubuntu-server).

- Schedule: weekdays 08:00 Bangkok time, job `27f143f1b1ae`, report pushed
  to LINE.
- LINE chat: the channel's webhook stays with the OpenClaw demo (both bots
  share one channel), so Hermes only pushes reports. Its webhook isn't
  published; the `hermes-line` tunnel was removed on 2026-10-22.
- The MacBook is retired: its Hermes gateway is uninstalled and the job is
  paused there. Use it only for development (tests, dry runs).

Open follow-ups are tracked in [PENDING.md](PENDING.md).

## URLs

| What | URL | Notes |
| --- | --- | --- |
| FinOps dashboard | <https://finops.praponb.com> | Cloudflare Access login; demo data for now |
| Hermes dashboard | <http://localhost:9119> | Through an SSH tunnel |
| Code on GitHub | <https://github.com/praponb/Hermes-Line> | Private |
| Databricks | <https://8259554454724321.1.gcp.databricks.com> | Workspace |
| LINE console | <https://developers.line.biz/console/> | Webhook settings |

- **FinOps dashboard:** charts and tables from the audit history (last 30
  days, Bangkok time). Cloudflare Access app `finops` (policy "Owner only")
  lets only prapon@gmail.com in, with a one-time code or another login
  method set up in Zero Trust. **Since 2026-10-22 it temporarily shows the
  demo dashboard** (made-up data, environment `Demo-Lakehouse`); see
  [Demo dashboard](#demo-dashboard). Real runs are still in the LINE reports
  and on the real dashboard through an SSH tunnel:
  `ssh -L 8650:localhost:8650 prapon@192.168.1.49`, then
  <http://localhost:8650>.
- **Hermes dashboard:** it listens on the server only (`127.0.0.1:9119`)
  because it stores API keys. Open the tunnel first and keep it open:
  `ssh -L 9119:localhost:9119 prapon@192.168.1.49`.
- **Code:** production runs from `main`; work happens on `b_pbv_main`.
- **Reports:** they arrive in the LINE chat, and past reports are saved on
  the server in `~/.hermes/cron/output/27f143f1b1ae/`. The FinOps dashboard
  shows the same findings without the remediation SQL.

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

The commands below work in a local checkout (development). For production,
run them on the server inside the container, as shown in
[Operate](#operate).

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
# Read-only dashboard over the history DB (default 127.0.0.1:8650)
python3 -m finops web [--host HOST] [--port PORT] [--db PATH]
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
scripts/audit_compute.sh dataproc moukda-lakehouse asia-southeast1
scripts/audit_storage.sh lakehouse-audit 1525087559b13da2 \
  "lakehouse.bronze lakehouse.silver lakehouse.gold"
python3 -m finops run --run-dir "$RUN_DIR" --db /tmp/finops/debug.db
# Notes recorded by the collectors
cat "$RUN_DIR/errors.jsonl"
```

`CLI_TIMEOUT` (default 120 s) limits each CLI call.

### Hermes schedule

The job is `lakehouse-finops-auditor`, id `27f143f1b1ae`. On the server,
prefix each `hermes` command with `docker compose exec gateway` (from
`~/Hermes-Line/deploy`).

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

`hermes cron run` starts the job on the next scheduler tick. Delivery is
logged in `~/.hermes/logs/` as `delivered to line:…`.

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

Timestamps in `audit_runs` are UTC. The server has no `sqlite3`; use the
container's Python instead:

```bash
docker compose exec gateway python3 -c "import sqlite3
c = sqlite3.connect('/opt/data/audit/lakehouse_finops.db')
q = 'SELECT * FROM audit_runs ORDER BY timestamp DESC LIMIT 10'
for row in c.execute(q): print(row)"
```

## Deploy with Docker (Ubuntu server)

Hermes and the auditor run together in one container on `192.168.1.49`
(user `prapon`). `deploy/docker-compose.yml` starts:

- `gateway`: Hermes (cron + LINE) with the auditor at `/opt/hermes-line`
- `dashboard`: Hermes dashboard on `127.0.0.1:9119` only
- `finops-web`: FinOps dashboard on `127.0.0.1:8650`, with only
  `~/.hermes/audit` mounted, read-only
- `finops-tunnel`: tunnel `hermes-finops`, `finops.praponb.com` →
  `localhost:8650` (`deploy/finops-tunnel.yml`; temporarily `localhost:8651`,
  the [demo dashboard](#demo-dashboard)). Cloudflare Access app
  `finops` allows only prapon@gmail.com, and cloudflared rejects requests
  without a valid Access token for that app.

Hermes data lives in `/home/prapon/.hermes` (mounted at `/opt/data`).
Secrets go in `deploy/secrets/` and `deploy/.env`, both git-ignored:

| File | Contents |
| --- | --- |
| `deploy/.env` | copy of `.env.example` with the `hermes-finops` tunnel token |
| `deploy/secrets/databrickscfg` | `[lakehouse-audit]` service principal |
| `deploy/secrets/gcp-sa.json` | key of `hermes-finops-auditor@…` |

Credentials used by the server (all read-only):

- Databricks service principal `hermes-finops-auditor` (application ID
  `e7764646-bd66-46bc-85b7-e9bb383360de`) in workspace
  `8259554454724321.1.gcp.databricks.com`: CAN_USE on warehouse
  `1525087559b13da2`; USE CATALOG `lakehouse`; USE SCHEMA and SELECT on
  `lakehouse.bronze/silver/gold`. Its OAuth secret expires on 2028-10-21.
- GCP service account
  `hermes-finops-auditor@moukda-lakehouse.iam.gserviceaccount.com` with
  `roles/dataproc.viewer`.
- Both were created by moukda77770919@gmail.com, which owns the workspace
  and the project.
- The LINE channel token comes from the Hermes `.env` in
  `/home/prapon/.hermes`.

Hermes runs the agent's commands inside the container
(`terminal.backend: local`), so no Docker socket is mounted. The LINE webhook
listens on `127.0.0.1:8646` only (`LINE_HOST=127.0.0.1` in the Hermes `.env`)
and isn't published, so Hermes can push to LINE but not receive chats.
`LINE_PUBLIC_URL` is unset, so Hermes can't send images, audio or video. Each container's log is capped at 3 × 10 MB.

### First install

```bash
# Base image: Hermes at commit f7b26ef (the version migrated from the Mac)
git clone https://github.com/NousResearch/hermes-agent.git ~/src/hermes-agent
git -C ~/src/hermes-agent checkout f7b26ef
docker build -t hermes-agent:f7b26ef ~/src/hermes-agent

# This repo (read-only deploy key, ssh alias github-hermes-line)
git clone -b b_pbv_main git@github-hermes-line:praponb/Hermes-Line.git \
  ~/Hermes-Line
cd ~/Hermes-Line/deploy
cp .env.example .env            # then add FINOPS_TUNNEL_TOKEN
chmod 600 .env secrets/*
docker compose build

# Restore Hermes data from a Mac backup (hermes backup -o ...)
mkdir -p ~/.hermes && cp ~/hermes-backup-migrate.zip ~/.hermes/
docker compose run --rm gateway import -f /opt/data/hermes-backup-migrate.zip

docker compose up -d
# One-time gcloud login as the service account (stored in CLOUDSDK_CONFIG)
docker compose exec -u 1000:1000 gateway gcloud auth \
  activate-service-account --key-file=/run/hermes-line-secrets/gcp-sa.json
docker compose exec -u 1000:1000 gateway \
  gcloud config set project moukda-lakehouse
# Regenerate the cron wrapper with container paths
docker compose exec -u 1000:1000 gateway \
  /opt/hermes-line/scripts/install_hermes.sh
```

### Operate

```bash
cd ~/Hermes-Line/deploy
docker compose ps
docker compose logs -f gateway
docker compose exec -u 1000:1000 gateway hermes cron list
docker compose exec -u 1000:1000 gateway \
  /opt/hermes-line/scripts/run_audit.sh --dry-run
# Real run saved to the history DB, as the scheduler runs it, without LINE
docker compose exec -T -u hermes -e HOME=/opt/data gateway \
  /opt/data/scripts/lakehouse_finops.sh
```

### Demo dashboard

Since 2026-10-22, `finops.praponb.com` shows a demo dashboard with made-up
data (environment `Demo-Lakehouse`, weekday 08:00 runs from 1 Oct). It is
separate from production:

- `~/hermes-demo/make_demo.py` (on the server, not in this repo) writes the
  JSON files the collectors would produce for each run and feeds them through
  the real rules, trend and DB code into `~/hermes-demo/demo.db`.
  `~/hermes-demo/demo.toml` is `config/auditor.toml` with the demo
  environment name.
- Container `hermes-finops-demo` (started with `docker run`, not compose)
  serves it read-only on `127.0.0.1:8651`.
- The audit, its history DB, the LINE reports and `finops-web` on 8650 are
  unchanged.

```bash
# Rebuild the demo data up to today (the page warns once the latest run is
# more than 4 days old, from 26 Oct with the current data)
docker run --rm --user 1000:1000 -e TZ=Asia/Bangkok \
  -e PYTHONPATH=/opt/hermes-line -w /opt/hermes-line \
  -v ~/hermes-demo:/demo --entrypoint python3 hermes-line:latest \
  /demo/make_demo.py
# (Re)start the demo dashboard
docker run -d --name hermes-finops-demo --restart unless-stopped \
  --network host --user 1000:1000 -e TZ=Asia/Bangkok -w /opt/hermes-line \
  -v ~/hermes-demo:/demo:ro --log-opt max-size=10m --log-opt max-file=3 \
  --entrypoint python3 hermes-line:latest -m finops --config /demo/demo.toml \
  web --host 127.0.0.1 --port 8651 --db /demo/demo.db
```

To switch `finops.praponb.com` back to the real dashboard, set the service in
`deploy/finops-tunnel.yml` to `http://127.0.0.1:8650`, merge and deploy, run
`docker compose restart finops-tunnel`, then remove the demo with
`docker rm -f hermes-finops-demo && rm -rf ~/hermes-demo`.

### Backups

`deploy/backup.sh` runs `hermes backup` daily at 03:00 (host crontab) and
keeps the newest 7 zips in `/home/prapon/hermes-backups`. Restore one with
`hermes import` (see First install). The zips contain the Hermes `.env`,
logins and the gcloud service-account login, so keep the folder private.

```bash
crontab -l                                  # shows the backup.sh line
~/Hermes-Line/deploy/backup.sh              # run one now
tail ~/hermes-backups/backup.log
```

### Update

```bash
cd ~/Hermes-Line && git pull
cd deploy && docker compose build && docker compose up -d
```

The server checkout tracks `main`.

### Upgrade Hermes

The base image is pinned with `HERMES_IMAGE` in `deploy/.env`. To move to a
newer Hermes commit:

```bash
cd ~/src/hermes-agent && git fetch && git checkout <commit>
docker build -t hermes-agent:<commit> .
# Back up first, then point deploy/.env at the new image
~/Hermes-Line/deploy/backup.sh
sed -i 's/^HERMES_IMAGE=.*/HERMES_IMAGE=hermes-agent:<commit>/' \
  ~/Hermes-Line/deploy/.env
cd ~/Hermes-Line/deploy && docker compose build && docker compose up -d
docker compose exec gateway hermes cron list   # job still active?
```

To undo, set `HERMES_IMAGE` back to the previous tag and repeat the last
line. The base build takes 15–45 minutes.

### Roll back to the Mac

Only one Hermes gateway may run for the LINE channel at a time.

```bash
# On the server
cd ~/Hermes-Line/deploy && docker compose down
# On the Mac
hermes gateway install
hermes cron resume 27f143f1b1ae
```

The Mac keeps its `~/.hermes` and `~/hermes-backup-migrate-20261013.zip`
from the cutover.

## Project layout

```text
config/auditor.toml        settings, thresholds, cost rates
finops/__main__.py         CLI (targets | get | run | web)
finops/config.py           TOML loader
finops/rules.py            compute and storage rules
finops/trends.py           rolling-baseline waste comparison
finops/report.py           LINE-sized report rendering
finops/web.py              read-only web dashboard (finops-web, demo dashboard)
finops/db.py               SQLite schema and persistence
scripts/run_audit.sh       entrypoint: preflight → collect → evaluate → report
scripts/preflight.sh       credential checks
scripts/audit_compute.sh   Module A collector
scripts/audit_storage.sh   Module B collector
scripts/lib.sh             shared shell helpers (timeouts, SQL statement API)
scripts/install_hermes.sh  installs the Hermes wrapper script
deploy/                    Dockerfile + compose for the Ubuntu server
tests/                     pytest suite
spec.docx                  original specification
```
