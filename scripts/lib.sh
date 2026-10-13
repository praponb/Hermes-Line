# Shared helpers for the FinOps collectors. Sourced, not executed. Bash 3.2 compatible (macOS /bin/bash).
# Callers must export RUN_DIR (ephemeral buffer dir) and may export FINOPS_DEADLINE (epoch seconds).

CLI_TIMEOUT=${CLI_TIMEOUT:-120}

log() {
  [ -n "${FINOPS_VERBOSE:-}" ] && printf '[finops %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2
  return 0
}

# with_timeout SECS CMD... : run CMD with stdin closed; SIGALRM kills it after SECS (macOS has no timeout(1)).
with_timeout() {
  local secs=$1
  shift
  perl -e 'alarm shift; exec @ARGV or die "exec $ARGV[0]: $!\n"' "$secs" "$@" </dev/null
}

# note LEVEL TARGET MESSAGE : record a collection problem for the report (level: warn | info).
note() {
  jq -nc --arg level "$1" --arg target "$2" --arg message "$3" \
    '{level: $level, target: $target, message: $message}' >>"$RUN_DIR/errors.jsonl"
}

# err_summary FILE : first non-empty line of a CLI error, trimmed for the report.
err_summary() {
  local line
  line=$(grep -v '^[[:space:]]*$' "$1" 2>/dev/null | head -1 | cut -c1-160)
  printf '%s' "${line:-no error output}"
}

past_deadline() {
  [ "$(date +%s)" -ge "${FINOPS_DEADLINE:-9999999999}" ]
}

# sql_rows PROFILE WAREHOUSE_ID STATEMENT : print the result as a JSON array of row objects.
# Uses the Statement Execution API (the CLI has no `sql execute` command).
sql_rows() {
  local profile=$1 warehouse=$2 statement=$3 resp state id polls=0
  resp=$(with_timeout "$CLI_TIMEOUT" databricks api post /api/2.0/sql/statements -p "$profile" --json "$(
    jq -nc --arg w "$warehouse" --arg s "$statement" \
      '{warehouse_id: $w, statement: $s, wait_timeout: "50s", on_wait_timeout: "CONTINUE",
        format: "JSON_ARRAY", disposition: "INLINE"}'
  )") || return 1
  state=$(jq -r '.status.state' <<<"$resp")
  id=$(jq -r '.statement_id' <<<"$resp")
  while [ "$state" = PENDING ] || [ "$state" = RUNNING ]; do
    polls=$((polls + 1))
    if [ "$polls" -gt 24 ] || past_deadline; then
      with_timeout 30 databricks api post "/api/2.0/sql/statements/$id/cancel" -p "$profile" >/dev/null 2>&1 || true
      echo "statement still $state after polling; cancelled" >&2
      return 1
    fi
    sleep 5
    resp=$(with_timeout "$CLI_TIMEOUT" databricks api get "/api/2.0/sql/statements/$id" -p "$profile") || return 1
    state=$(jq -r '.status.state' <<<"$resp")
  done
  if [ "$state" != SUCCEEDED ]; then
    jq -r '.status.error.message // .status.state' <<<"$resp" >&2
    return 1
  fi
  jq -c '[.manifest.schema.columns[].name] as $c
    | [(.result.data_array // [])[] | [$c, .] | transpose | map({(.[0]): .[1]}) | add]' <<<"$resp"
}
