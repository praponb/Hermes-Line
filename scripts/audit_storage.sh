#!/usr/bin/env bash
# Module B collector: DESCRIBE DETAIL + DESCRIBE HISTORY for every Delta table in the configured
# schemas, written to $RUN_DIR/storage_PROFILE.json. Read-only (DESCRIBE statements only).
# Usage: audit_storage.sh PROFILE WAREHOUSE_ID "catalog.schema catalog.schema ..."
set -euo pipefail
. "$(dirname "$0")/lib.sh"

profile=$1 warehouse=$2 schemas=$3
target="databricks:$profile"
if [ "$warehouse" = "-" ] || [ "$schemas" = "-" ]; then
  note info "$target" "no warehouse_id/schemas configured; storage audit skipped"
  exit 0
fi

rows="$RUN_DIR/storage_$profile.jsonl"
: >"$rows"
for fq in $schemas; do
  catalog=${fq%%.*} schema=${fq#*.}
  tables="$RUN_DIR/tables_$profile.raw.json"
  if ! with_timeout "$CLI_TIMEOUT" databricks tables list "$catalog" "$schema" -p "$profile" -o json \
    >"$tables" 2>"$RUN_DIR/err"; then
    note warn "$target" "tables list $fq failed: $(err_summary "$RUN_DIR/err")"
    continue
  fi
  jq -r '.[] | select((.data_source_format // "") == "DELTA"
    and (.table_type == "MANAGED" or .table_type == "EXTERNAL")) | .full_name' "$tables" >"$RUN_DIR/names"
  while read -r table; do
    if past_deadline; then
      note warn "$target" "time limit reached; remaining tables skipped"
      break 2
    fi
    quoted=$(awk -F. '{ printf "`%s`.`%s`.`%s`", $1, $2, $3 }' <<<"$table")
    if ! sql_rows "$profile" "$warehouse" "DESCRIBE DETAIL $quoted" >"$RUN_DIR/detail.json" 2>"$RUN_DIR/err"; then
      note warn "$target" "DESCRIBE DETAIL $table failed: $(err_summary "$RUN_DIR/err")"
      continue
    fi
    if ! sql_rows "$profile" "$warehouse" "DESCRIBE HISTORY $quoted LIMIT 500" >"$RUN_DIR/history.json" 2>"$RUN_DIR/err"; then
      note warn "$target" "DESCRIBE HISTORY $table failed (VACUUM check skipped): $(err_summary "$RUN_DIR/err")"
      echo '[]' >"$RUN_DIR/history.json"
    fi
    jq -nc --arg t "$table" --slurpfile d "$RUN_DIR/detail.json" --slurpfile h "$RUN_DIR/history.json" '{
        table: $t,
        detail: ($d[0][0] // {} | {numFiles, sizeInBytes, properties, partitionColumns, clusteringColumns}),
        history: [$h[0][] | {version, timestamp, operation, operationMetrics}]
      }' >>"$rows"
  done <"$RUN_DIR/names"
done
jq -s '.' "$rows" >"$RUN_DIR/storage_$profile.json"
log "$target: $(jq length "$RUN_DIR/storage_$profile.json") tables"
