#!/bin/bash
set -euo pipefail

BACKUP_TIME=$(date +%Y%m%d_%H%M%S)
POSTGRES_SERVER=${POSTGRES_SERVER:-docmate-postgres-1}
POSTGRES_USER=${POSTGRES_USER:-docmate}
POSTGRES_DB=${POSTGRES_DB:-docmate}
BACKUP_DIR=${BACKUP_DIR:-/home/docmate/backups}
RETENTION_DAYS=${RETENTION_DAYS:-14}
DUMP_NAME="docmate_backup_$BACKUP_TIME.dump"

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] Starting backup of $POSTGRES_DB from $POSTGRES_SERVER..."
docker exec -t "$POSTGRES_SERVER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c -f "/tmp/$DUMP_NAME"
docker cp "$POSTGRES_SERVER:/tmp/$DUMP_NAME" "$BACKUP_DIR/"
docker exec "$POSTGRES_SERVER" rm -f "/tmp/$DUMP_NAME"

# Prune dumps older than RETENTION_DAYS so BACKUP_DIR doesn't grow unbounded.
find "$BACKUP_DIR" -name 'docmate_backup_*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "[$(date -Iseconds)] Backup completed: $BACKUP_DIR/$DUMP_NAME"

