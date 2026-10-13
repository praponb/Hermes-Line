#!/usr/bin/env bash
# Daily backup of the server's Hermes data: `hermes backup` inside the gateway container,
# keeping the newest 7 zips in $HERMES_BACKUPS on the host (/backups in the container).
# Host crontab: 0 3 * * * $HOME/Hermes-Line/deploy/backup.sh >> $HOME/hermes-backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

name="hermes-backup-$(date +%Y%m%d-%H%M).zip"
echo "[$(date '+%F %T %Z')] backing up to $name"
docker compose exec -T gateway hermes backup -o "/backups/$name" -k 7 | tail -1
