# Pending items

Last updated: 2026-10-13 (after the move to the Ubuntu server). Times are
Bangkok time.

## Scheduled checks

- **1. Wednesday 2026-10-14, early morning.** The server's first daily backup
  runs at 03:00, then the first scheduled report at 08:00. Expect exactly
  one LINE message. Check on the server:

  ```bash
  cd ~/Hermes-Line/deploy
  tail ~/hermes-backups/backup.log
  docker compose exec gateway hermes cron runs 27f143f1b1ae --limit 3
  grep -h "delivered to line" ~/.hermes/logs/*.log | tail -3
  ```

- **2. Around 2026-10-20: Mac clean-up**, if the server has been stable:
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
- **5. gcloud on the Mac.** It can't sign in; production isn't affected. Fix
  with `gcloud auth login prapon@gmail.com --force` when needed.

## Possible later

- **6. Chat with Hermes on LINE.** Needs its own LINE channel; today the
  shared channel's webhook goes to the OpenClaw demo.

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
