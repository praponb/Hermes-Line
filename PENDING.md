# Pending items

Last updated: 2026-10-22. Times are Bangkok time.

## Scheduled checks

- **2. Mac clean-up**, once the audit has run cleanly on the new lakehouse:
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

## Possible later

- **6. Chat with Hermes on LINE.** Needs its own LINE channel; today the
  shared channel's webhook goes to the OpenClaw demo. It would also need a
  public HTTPS URL for the webhook again (a tunnel) and `LINE_PUBLIC_URL`.

## Done on 2026-10-22

- gcloud on the Mac uses moukda77770919@gmail.com (CLI login, Application
  Default Credentials and the VS Code extension all verified); the
  prapon@gmail.com login is no longer needed.
- Removed the unused `hermes-line` tunnel, its `hermes-line.praponb.com` DNS
  record and its token; `LINE_PUBLIC_URL` commented out in the Hermes `.env`.
- Audit failures since 2026-10-19 explained: GCP project
  `praponb-lakehouse` and its Databricks workspace were deleted (the project
  is purged by Google about 30 days later). The auditor now targets
  workspace `8259554454724321.1.gcp.databricks.com` and project
  `moukda-lakehouse`, with new read-only credentials created by
  moukda77770919@gmail.com: service principal `hermes-finops-auditor`
  (OAuth secret expires 2028-10-21) and service account
  `hermes-finops-auditor@moukda-lakehouse`.

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
