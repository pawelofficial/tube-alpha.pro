"""Admin SQL endpoint — password-protected raw SQL access to both databases."""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tube_alpha.database import Database
from tube_alpha.routers.dependencies import get_settings, require_admin_key

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SCHEMA_FILE = BASE_DIR / "schema.json"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class SqlRequest(BaseModel):
    db: str   # "data" or "admin"
    sql: str
    params: list[Any] = []


def _get_db(db_name: str) -> Database:
    if db_name not in ("data", "admin"):
        raise HTTPException(status_code=400, detail="db must be 'data' or 'admin'")
    settings = get_settings()
    path = settings.data_db_path if db_name == "data" else settings.admin_db_path
    return Database(path)


@router.post("/sql")
def run_sql(body: SqlRequest, _: None = Depends(require_admin_key)):
    """Execute arbitrary SQL against data.sqlite or admin.sqlite.

    Returns rows for SELECT, rowcount for writes.
    Pass params as a JSON array matching ? placeholders.
    """
    db = _get_db(body.db)
    sql = body.sql.strip()
    params = tuple(body.params)

    is_select = sql.upper().startswith("SELECT") or sql.upper().startswith("PRAGMA")
    try:
        if is_select:
            rows = db.fetch_all(sql, params)
            return {"rows": rows, "count": len(rows)}
        else:
            cursor = db.execute(sql, params)
            logger.info("Admin SQL on %s.sqlite: %s | params=%s | rowcount=%s",
                        body.db, sql, params, cursor.rowcount)
            return {"rowcount": cursor.rowcount}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tables")
def list_tables(db: str = "data", _: None = Depends(require_admin_key)):
    """List all tables and views in the chosen database."""
    database = _get_db(db)
    rows = database.fetch_all(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
    )
    return {"db": db, "objects": rows}


@router.post("/schema/init")
def init_schema(db: str = "data", _: None = Depends(require_admin_key)):
    """Create tables that don't exist yet. Safe — never drops or overwrites data."""
    database = _get_db(db)
    database.init_schema(SCHEMA_FILE)
    return {"db": db, "message": "Schema init complete"}


@router.post("/schema/recreate")
def recreate_schema(db: str = "data", _: None = Depends(require_admin_key)):
    """Drop and recreate all tables. DESTRUCTIVE — wipes all data in the chosen database."""
    database = _get_db(db)
    database.create_schema(SCHEMA_FILE)
    return {"db": db, "message": "Schema recreated — all data wiped"}
