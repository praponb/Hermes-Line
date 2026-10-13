#!/usr/bin/env bash
# Module A collector: list clusters and normalize them with jq into $RUN_DIR/compute_*.json.
# Usage: audit_compute.sh databricks PROFILE | audit_compute.sh dataproc PROJECT REGION
# Read-only. On CLI failure, records a note and exits 0 so other targets still run.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

case "$1" in
databricks)
  profile=$2
  target="databricks:$profile"
  raw="$RUN_DIR/clusters_$profile.raw.json"
  types="$RUN_DIR/node_types_$profile.raw.json"
  if ! with_timeout "$CLI_TIMEOUT" databricks clusters list -p "$profile" -o json >"$raw" 2>"$RUN_DIR/err"; then
    note warn "$target" "clusters list failed: $(err_summary "$RUN_DIR/err")"
    exit 0
  fi
  # Node-type core counts drive the OVERSIZED_DEV rule; missing counts just disable that rule.
  with_timeout "$CLI_TIMEOUT" databricks clusters list-node-types -p "$profile" -o json >"$types" 2>/dev/null ||
    echo '{"node_types": []}' >"$types"
  jq --arg p "$profile" --slurpfile types "$types" '
    ($types[0].node_types // [] | map({key: .node_type_id, value: .num_cores}) | from_entries) as $cores
    | [.[] | (.driver_node_type_id // .node_type_id // "") as $driver | {
        platform: "databricks",
        profile: $p,
        cluster_id,
        cluster_name,
        state,
        cluster_source,
        autotermination_minutes: (.autotermination_minutes // 0),
        num_workers: (.num_workers // 0),
        autoscale_min: .autoscale.min_workers,
        autoscale_max: .autoscale.max_workers,
        node_type_id,
        driver_node_type_id: $driver,
        driver_cores: ($cores[$driver] // null | if . == null then null else floor end),
        start_time,
        last_restarted_time,
        last_activity_time
      }]' "$raw" >"$RUN_DIR/compute_databricks_$profile.json"
  log "$target: $(jq length "$RUN_DIR/compute_databricks_$profile.json") clusters"
  ;;
dataproc)
  project=$2 region=$3
  target="dataproc:$project/$region"
  raw="$RUN_DIR/dataproc_$region.raw.json"
  if ! with_timeout "$CLI_TIMEOUT" gcloud dataproc clusters list --project "$project" --region "$region" \
    --format=json --quiet >"$raw" 2>"$RUN_DIR/err"; then
    if grep -q SERVICE_DISABLED "$RUN_DIR/err"; then
      # Not an error: Dataproc simply isn't used in this project.
      note info "$target" "Dataproc API not enabled; nothing to audit"
      echo '[]' >"$RUN_DIR/compute_dataproc_$region.json"
    else
      note warn "$target" "dataproc clusters list failed: $(err_summary "$RUN_DIR/err")"
    fi
    exit 0
  fi
  jq --arg p "$project" --arg r "$region" '[.[] | {
      platform: "dataproc",
      project: $p,
      region: $r,
      cluster_id: .clusterUuid,
      cluster_name: .clusterName,
      state: .status.state,
      state_start_time: .status.stateStartTime,
      num_workers: (.config.workerConfig.numInstances // 0),
      master_machine_type: (.config.masterConfig.machineTypeUri // "" | split("/") | last),
      worker_machine_type: (.config.workerConfig.machineTypeUri // "" | split("/") | last),
      idle_delete_ttl: .config.lifecycleConfig.idleDeleteTtl
    }]' "$raw" >"$RUN_DIR/compute_dataproc_$region.json"
  log "$target: $(jq length "$RUN_DIR/compute_dataproc_$region.json") clusters"
  ;;
*)
  echo "usage: audit_compute.sh databricks PROFILE | dataproc PROJECT REGION" >&2
  exit 64
  ;;
esac
