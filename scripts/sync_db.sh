#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../data"

for db in "$DATA_DIR"/*.sqlite; do
    win_db="$(cygpath -w "$db")"
    echo "Checkpointing $win_db..."
    python -c "
import sqlite3
c = sqlite3.connect(r'$win_db')
c.execute('PRAGMA wal_checkpoint(FULL)')
c.commit()
c.close()
"
    echo "Done"
done
