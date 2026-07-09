#!/usr/bin/env bash
# Nightly pg_dump (DESIGN §4). VPS cron: 0 3 * * * /path/to/scripts/backup.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/leadfinder}"
mkdir -p "$BACKUP_DIR"

docker exec leadfinder-postgres pg_dump -U leadfinder leadfinder \
  | gzip > "$BACKUP_DIR/leadfinder-$(date +%F).sql.gz"

# keep two weeks
find "$BACKUP_DIR" -name 'leadfinder-*.sql.gz' -mtime +14 -delete
echo "backup written: $BACKUP_DIR/leadfinder-$(date +%F).sql.gz"
