# Pending items

Last updated: 2026-10-22. Times are Bangkok time.

## Urgent

- **0. The daily audit has failed since Mon 2026-10-19.** The last good run
  was Fri 2026-10-16 08:01; every weekday run since exits with code 2:
  - Databricks: `default auth: cannot configure default credentials` (the
    `lakehouse-audit` profile from `deploy/secrets/databrickscfg` is not
    being picked up).
  - GCP: `PERMISSION_DENIED` on project `praponb-lakehouse` for
    `hermes-finops-auditor@…` (check its `roles/dataproc.viewer` grant).

  Details: `docker compose exec gateway hermes cron runs 27f143f1b1ae`.
  The dashboard at https://finops.praponb.com warns while data is stale.

## Scheduled checks

- **2. Mac clean-up**, once the audit runs cleanly again:
  - Delete `~/hermes-backup-migrate-20261013.zip`. It contains secrets,
    including the live LINE token.
  - Optionally remove the Mac's `~/.hermes` and its Hermes sandbox images
    (`nousresearch/hermes-sandbox:desktop`, `hermes-lakehouse-sandbox`,
    about 4.7 GB).

  After this, rolling back to the Mac is no longer possible.

## Optional

- **3. Off-server backups.** Daily backups stay on the server in
  `~/hermes-backups`. A copy elsewhere (NAS, cloud storage) needs a
  destination.
- **4. Unused tunnel.** Keep `hermes-line.praponb.com` for a future Hermes
  LINE channel, or remove the `hermes-line` tunnel and its DNS record.
  (`finops.praponb.com` uses its own tunnel, `hermes-finops`.)
- **5. gcloud on the Mac.** It can't sign in; production isn't affected. Fix
  with `gcloud auth login prapon@gmail.com --force` when needed.

## Possible later

- **6. Chat with Hermes on LINE.** Needs its own LINE channel; today the
  shared channel's webhook goes to the OpenClaw demo.

## Done on 2026-10-22

- First server backup and reports checked: backups run daily at 03:00
  (7 kept); reports ran on 14–16 Oct.
- FinOps dashboard at https://finops.praponb.com (PR #5), behind
  Cloudflare Access app `finops` (prapon@gmail.com only).

## Done on 2026-10-13

- Cost rates set from Databricks (Premium plan) and GCP list prices.
- Dataproc API enabled; `asia-southeast3` (Bangkok) added to the audit.
- Old prototype token file deleted and its Databricks PAT revoked.
- Hermes and the auditor moved to Docker on `192.168.1.49`, with read-only
  Databricks and GCP credentials.
- Server hardening: log caps, daily backups, local-only webhook, build cache
  pruned, reboot test passed.
- README updated with the runbook and URLs; PRs #1–#3 merged, server tracks
  `main`.
