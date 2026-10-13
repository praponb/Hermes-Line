#!/usr/bin/env bash
# Verify ambient CLI credentials before collecting (spec §6 "Environment Validation").
# Usage: preflight.sh databricks PROFILE | preflight.sh gcloud
# Exit 0 when usable; otherwise records a note and exits 1. Tokens never reach stdout.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

err="$RUN_DIR/preflight.err"
case "$1" in
databricks)
  if ! with_timeout 60 databricks current-user me -p "$2" -o json >/dev/null 2>"$err"; then
    note warn "databricks:$2" "auth check failed: $(err_summary "$err")"
    exit 1
  fi
  ;;
gcloud)
  if ! command -v gcloud >/dev/null; then
    note warn "gcp" "gcloud not on PATH; Dataproc audit skipped"
    exit 1
  fi
  if ! with_timeout 60 gcloud auth print-access-token --quiet >/dev/null 2>"$err"; then
    note warn "gcp" "gcloud auth check failed: $(err_summary "$err")"
    exit 1
  fi
  ;;
*)
  echo "usage: preflight.sh databricks PROFILE | gcloud" >&2
  exit 64
  ;;
esac
log "preflight ok: $*"
