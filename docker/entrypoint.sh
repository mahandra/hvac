#!/bin/sh
set -e

echo "Waiting for the database at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
python - <<'PY'
import os, sys, time
import psycopg

params = dict(
    host=os.environ.get("POSTGRES_HOST", "db"),
    port=os.environ.get("POSTGRES_PORT", "5432"),
    dbname=os.environ.get("POSTGRES_DB", "hvac"),
    user=os.environ.get("POSTGRES_USER", "hvac"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    connect_timeout=3,
)
for attempt in range(60):
    try:
        psycopg.connect(**params).close()
        print("Database is ready.")
        sys.exit(0)
    except psycopg.OperationalError as exc:
        print(f"  not ready yet ({exc.__class__.__name__}), retrying...")
        time.sleep(2)
print("Database did not become ready in time.", file=sys.stderr)
sys.exit(1)
PY

MEDIA_DIR="${MEDIA_ROOT:-/app/data/media}"
if ! touch "$MEDIA_DIR/.write-test" 2>/dev/null; then
    echo "ERROR: $MEDIA_DIR is not writable by uid $(id -u)." >&2
    echo "Fix on the host: sudo chown -R $(id -u):$(id -g) data/media  (or set APP_UID/APP_GID in .env and rebuild)" >&2
    exit 1
fi
rm -f "$MEDIA_DIR/.write-test"

python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

XLSX="${AUTO_IMPORT_XLSX:-/app/data/import/properties.xlsx}"
if [ -f "$XLSX" ]; then
    python manage.py import_properties "$XLSX" --if-empty
fi

exec "$@"
