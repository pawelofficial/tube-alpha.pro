#!/bin/bash
set -e

mkdir -p /app/data

echo "Restoring databases from GCS..."
litestream restore -if-replica-exists -config /etc/litestream.yml /app/data/data.sqlite
litestream restore -if-replica-exists -config /etc/litestream.yml /app/data/admin.sqlite
echo "Restore complete."

exec litestream replicate -config /etc/litestream.yml \
  -exec "gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT --timeout 120 --log-level info"
