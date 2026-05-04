"""Admin SQL endpoint — password-protected raw SQL access to both databases."""

import os
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from tube_alpha.database import Database
from tube_alpha.routers.dependencies import get_settings

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


def _check_key(x_admin_key: str) -> None:
    secret = os.environ.get("ADMIN_SECRET_KEY", "")
    if not secret:
        raise HTTPException(status_code=503, detail="ADMIN_SECRET_KEY not configured")
    if x_admin_key != secret:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.post("/sql")
def run_sql(body: SqlRequest, x_admin_key: str = Header(...)):
    """Execute arbitrary SQL against data.sqlite or admin.sqlite.

    Returns rows for SELECT, rowcount for writes.
    Pass params as a JSON array matching ? placeholders.
    """
    _check_key(x_admin_key)
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
def list_tables(db: str = "data", x_admin_key: str = Header(...)):
    """List all tables and views in the chosen database."""
    _check_key(x_admin_key)
    database = _get_db(db)
    rows = database.fetch_all(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
    )
    return {"db": db, "objects": rows}
