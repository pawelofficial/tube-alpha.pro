#!/bin/bash
set -e

mkdir -p /app/data

GUNICORN_CMD="gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT --timeout 120 --log-level info"

if [ -z "$LITESTREAM_BUCKET" ]; then
  echo "LITESTREAM_BUCKET not set — skipping Litestream (local dev mode)."
  exec $GUNICORN_CMD
fi

echo "Restoring databases from GCS..."
litestream restore -if-replica-exists -config /etc/litestream.yml /app/data/data.sqlite
litestream restore -if-replica-exists -config /etc/litestream.yml /app/data/admin.sqlite
echo "Restore complete."

exec litestream replicate -config /etc/litestream.yml -exec "$GUNICORN_CMD"
