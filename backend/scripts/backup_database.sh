#!/bin/bash

BACKUP_TIME=`date +%Y%m%d_%H%M%S`
POSTGRES_SERVER=docmate-postgres-1
POSTGRES_USER=docmate
POSTGRES_DB=docmate
BACKUP_DIR=~dropnock/projects/backup

# Perform backup
echo "Starting backup... "
docker exec -t $POSTGRES_SERVER pg_dump -U $POSTGRES_USER -d $POSTGRES_DB -F c -f /tmp/docmate_backup_$BACKUP_TIME.dump
docker cp $POSTGRES_SERVER:/tmp/docmate_backup_$BACKUP_TIME.dump $BACKUP_DIR
echo "Backup completed"

