"""Database layer with parameterized queries.

Provides a Database class that wraps SQLite with:
- Parameterized queries (no SQL injection)
- Context manager support
- Transaction helpers
- Schema management

Usage:
    from tube_alpha.database import Database
    from tube_alpha.config import Settings

    settings = Settings()
    db = Database(settings.data_db_path)

    # Read
    rows = db.fetch_all("SELECT * FROM channels WHERE video_id = ?", (video_id,))

    # Write
    db.execute("INSERT INTO channels (name, video_id) VALUES (?, ?)", (name, vid))

    # Context manager for transactions
    with db.transaction():
        db.execute(...)
        db.execute(...)
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions."""
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single statement (INSERT, UPDATE, DELETE)."""
        try:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor
        except Exception as e:
            logger.warning("SQL failed: %s params=%s error=%s", sql, params, e)
            self.conn.rollback()
            raise

    def execute_many(self, sql: str, param_list: List[tuple]) -> None:
        """Execute a statement with multiple parameter sets in a transaction."""
        with self.transaction():
            self.conn.executemany(sql, param_list)

    def fetch_all(
        self, sql: str, params: tuple = (), columns: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Execute SELECT and return list of dicts."""
        try:
            cursor = self.conn.execute(sql, params)
            rows = cursor.fetchall()

            if columns:
                col_names = columns
            elif cursor.description:
                col_names = [desc[0] for desc in cursor.description]
            else:
                return []

            return [dict(zip(col_names, row)) for row in rows]
        except Exception as e:
            logger.warning("Select failed: %s params=%s error=%s", sql, params, e)
            return []

    def fetch_one(
        self, sql: str, params: tuple = ()
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT and return first row as dict, or None."""
        try:
            cursor = self.conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            if cursor.description:
                col_names = [desc[0] for desc in cursor.description]
                return dict(zip(col_names, row))
            return None
        except Exception as e:
            logger.warning("Select failed: %s params=%s error=%s", sql, params, e)
            return None

    def fetch_scalar(self, sql: str, params: tuple = ()) -> Any:
        """Execute SELECT and return single value, or None."""
        try:
            cursor = self.conn.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning("Select failed: %s params=%s error=%s", sql, params, e)
            return None

    def fetch_scalars(self, sql: str, params: tuple = ()) -> List[Any]:
        """Execute SELECT and return list of first-column values."""
        try:
            cursor = self.conn.execute(sql, params)
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning("Select failed: %s params=%s error=%s", sql, params, e)
            return []

    def create_schema(self, schema_file: Path) -> None:
        """Create tables from schema.json definition."""
        with open(schema_file) as f:
            schema = json.load(f)

        db_filename = self.db_path.stem  # "data" or "admin"

        for view in schema.get("views", []):
            view_name = view["name"]
            if view.get("db_type", "data") != db_filename:
                continue
            logger.info("Dropping view: %s", view_name)
            self.conn.execute(view["drop_view"])

        for table in schema["tables"]:
            table_name = table["name"]
            db_type = table.get("db_type", "data")

            if db_type != db_filename:
                continue

            logger.info("Creating table: %s", table_name)
            self.conn.execute(table["drop_table"])
            self.conn.execute(table["create_table"])

        for view in schema.get("views", []):
            view_name = view["name"]
            if view.get("db_type", "data") != db_filename:
                continue
            logger.info("Creating view: %s", view_name)
            self.conn.execute(view["create_view"])

        self.conn.commit()
        logger.info("Schema creation complete for %s", self.db_path.name)

    def init_schema(self, schema_file: Path) -> None:
        """Create tables/views if they don't already exist. Safe to call on every startup."""
        with open(schema_file) as f:
            schema = json.load(f)

        db_filename = self.db_path.stem

        for table in schema["tables"]:
            if table.get("db_type", "data") != db_filename:
                continue
            if not self.table_exists(table["name"]):
                sql = table["create_table"].replace("create table ", "create table if not exists ", 1)
                self.conn.execute(sql)
                logger.info("Created table: %s", table["name"])

        for view in schema.get("views", []):
            if view.get("db_type", "data") != db_filename:
                continue
            self.conn.execute(view["drop_view"])
            self.conn.execute(view["create_view"])

        self.conn.commit()
        logger.info("Schema init complete for %s", self.db_path.name)

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        result = self.fetch_scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return result is not None
