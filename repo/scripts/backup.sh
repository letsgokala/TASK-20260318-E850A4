#!/usr/bin/env bash
# Daily backup script — pg_dump + rsync of on-disk evidence.
# Schedule via cron: 0 2 * * * /app/scripts/backup.sh
#
# The audit report flagged that the previous version only backed up
# /storage/materials, leaving finance invoice attachments out of
# recovery. Invoices are now copied into a sibling subtree under each
# dated backup directory, and the restore endpoint pulls them back in.
set -euo pipefail

DATE=$(date +%Y%m%d)
DB_BACKUP_DIR="/backups/db"
FILE_BACKUP_DIR="/backups/files/${DATE}"
MATERIALS_BACKUP_DIR="${FILE_BACKUP_DIR}/materials"
INVOICES_BACKUP_DIR="${FILE_BACKUP_DIR}/invoices"
RETENTION_DAYS=30

# Database settings (override via environment)
DB_NAME="${POSTGRES_DB:-eagle_point}"
DB_USER="${POSTGRES_USER:-app_user}"
DB_HOST="${DB_HOST:-db}"

echo "[$(date)] Starting backup for ${DATE}..."

# Ensure backup directories exist
mkdir -p "${DB_BACKUP_DIR}"
mkdir -p "${MATERIALS_BACKUP_DIR}"
mkdir -p "${INVOICES_BACKUP_DIR}"

# Database backup
echo "[$(date)] Backing up database..."
pg_dump \
    --host="${DB_HOST}" \
    --username="${DB_USER}" \
    --format=custom \
    --file="${DB_BACKUP_DIR}/backup_${DATE}.dump" \
    "${DB_NAME}"
echo "[$(date)] Database backup complete: ${DB_BACKUP_DIR}/backup_${DATE}.dump"

# Material file backup
echo "[$(date)] Backing up material files..."
if [ -d "/storage/materials" ]; then
    rsync -a /storage/materials/ "${MATERIALS_BACKUP_DIR}/"
    echo "[$(date)] Material file backup complete: ${MATERIALS_BACKUP_DIR}/"
else
    echo "[$(date)] No material files directory found, skipping."
fi

# Invoice file backup (finance evidence — previously omitted)
echo "[$(date)] Backing up invoice files..."
if [ -d "/storage/invoices" ]; then
    rsync -a /storage/invoices/ "${INVOICES_BACKUP_DIR}/"
    echo "[$(date)] Invoice file backup complete: ${INVOICES_BACKUP_DIR}/"
else
    echo "[$(date)] No invoice files directory found, skipping."
fi

# Cleanup old backups
echo "[$(date)] Cleaning up backups older than ${RETENTION_DAYS} days..."
find "${DB_BACKUP_DIR}" -name "backup_*.dump" -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
find /backups/files -mindepth 1 -maxdepth 1 -type d -mtime +${RETENTION_DAYS} -exec rm -rf {} \; 2>/dev/null || true

echo "[$(date)] Backup complete."
