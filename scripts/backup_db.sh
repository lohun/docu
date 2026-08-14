#!/usr/bin/env bash
# Nightly database backup for docversion.
#
# Targets whatever database DOCVERSION_DATABASE_URL points at (local Postgres
# or the Supabase pooler). Dumps to a timestamped file under the configured
# directory and prunes dumps older than RETENTION_DAYS (default 14).
#
# Free-tier Supabase has no PITR/continuous backups, so this external dump is
# the safety net for the DB being the source of truth.
#
# Usage:
#   DOCVERSION_DATABASE_URL=... DOCVERSION_DATABASE_SSLMODE=require \
#     scripts/backup_db.sh /var/lib/docversion/backups
#
# Run from a systemd timer/cron. Reads .env if DOCVERSION_ENV_FILE is set.

set -euo pipefail

BACKUP_DIR="${1:?usage: backup_db.sh <backup-dir>}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

# Load the app settings into this shell (same precedence as app/config.py).
if [[ -n "${DOCVERSION_ENV_FILE:-}" && -f "$DOCVERSION_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$DOCVERSION_ENV_FILE"
  set +a
fi

DB_URL="${DOCVERSION_DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "error: DOCVERSION_DATABASE_URL is not set" >&2
  exit 1
fi

# The app URL uses the asyncpg driver; pg_dump wants a plain postgres:// URL.
DUMP_URL="${DB_URL/postgresql+asyncpg:\/\//postgresql://}"
DUMP_URL="${DUMP_URL/postgresql+psycopg2:\/\//postgresql://}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTFILE="$BACKUP_DIR/docversion-${STAMP}.sql"

echo "backup: dumping $DUMP_URL -> $OUTFILE"
pg_dump --dbname="$DUMP_URL" --format=custom --file="$OUTFILE"

# Prune old dumps.
find "$BACKUP_DIR" -maxdepth 1 -name 'docversion-*.sql' -mtime "+${RETENTION_DAYS}" \
  -exec rm -f {} \;

echo "backup: done (retention ${RETENTION_DAYS}d)"
