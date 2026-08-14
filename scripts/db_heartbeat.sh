#!/usr/bin/env bash
# Database heartbeat probe.
#
# Free-tier Supabase auto-pauses compute after ~1 week of inactivity. This
# probe issues a cheap query so activity is continuous and fails loudly (exit
# code 1, alertable via systemd/cron) if the DB ever becomes unreachable —
# including a paused project. Run every few minutes via a systemd timer.
#
# Usage:
#   DOCVERSION_DATABASE_URL=... DOCVERSION_DATABASE_SSLMODE=require \
#     scripts/db_heartbeat.sh

set -euo pipefail

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

PING_URL="${DB_URL/postgresql+asyncpg:\/\//postgresql://}"
PING_URL="${PING_URL/postgresql+psycopg2:\/\//postgresql://}"

psql --dbname="$PING_URL" --tuples-only --no-align --command="SELECT 1;" >/dev/null \
  && echo "heartbeat: database ok" \
  || { echo "heartbeat: DATABASE UNREACHABLE" >&2; exit 1; }
