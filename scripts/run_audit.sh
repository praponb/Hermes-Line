#!/usr/bin/env bash
# Hermes cron entrypoint (spec §6): preflight → collect → evaluate/persist → triage report on stdout.
# Hermes (--no-agent) delivers stdout to LINE verbatim; a non-zero exit becomes a failure alert.
# Usage: scripts/run_audit.sh [--dry-run]    (--dry-run persists to /tmp/finops/dry-run.db instead)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$REPO/scripts"
. "$SCRIPTS/lib.sh"
cd "$REPO"

DB_PATH=$(python3 -m finops get db_path)
if [ "${1:-}" = "--dry-run" ]; then
  DB_PATH=/tmp/finops/dry-run.db
fi

STARTED=$(date +%s)
TIMEOUT=$(python3 -m finops get timeout_sec)
# Collectors stop starting new work a minute before the hard limit to leave time for the report.
export FINOPS_DEADLINE=$((STARTED + TIMEOUT - 60))
export RUN_DIR="/tmp/finops/$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
trap 'rm -rf "$RUN_DIR"' EXIT

python3 -m finops targets >"$RUN_DIR/targets.tsv"
gcp_ok=""
while IFS=$'\t' read -r kind a b c; do
  case "$kind" in
  databricks)
    "$SCRIPTS/preflight.sh" databricks "$a" || continue
    "$SCRIPTS/audit_compute.sh" databricks "$a" || note warn "databricks:$a" "compute collector crashed"
    "$SCRIPTS/audit_storage.sh" "$a" "$b" "$c" || note warn "databricks:$a" "storage collector crashed"
    ;;
  dataproc)
    if [ -z "$gcp_ok" ]; then
      if "$SCRIPTS/preflight.sh" gcloud; then gcp_ok=yes; else gcp_ok=no; fi
    fi
    if [ "$gcp_ok" = yes ]; then
      "$SCRIPTS/audit_compute.sh" dataproc "$a" "$b" || note warn "dataproc:$a/$b" "compute collector crashed"
    fi
    ;;
  esac
done <"$RUN_DIR/targets.tsv"

python3 -m finops run --run-dir "$RUN_DIR" --db "$DB_PATH" --started-at "$STARTED"
