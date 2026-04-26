"""Database query tool router.

Provides a read-only SQL query endpoint for admin/debugging.
Only SELECT queries are allowed.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from tube_alpha.config import Settings
from tube_alpha.database import Database
from tube_alpha.models import SQLQueryRequest
from tube_alpha.routers.dependencies import get_auth_service, get_settings
from tube_alpha.services.auth import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


@router.post("/query")
async def execute_query(
    body: SQLQueryRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Execute a read-only SQL SELECT query against the data database.

    Requires authentication. Only SELECT statements are allowed.
    """
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")

    logger.info("Query from %s: %s", email, body.sql[:100])

    db = Database(settings.data_db_path)
    try:
        rows = db.fetch_all(body.sql)
        columns = list(rows[0].keys()) if rows else []
        return {
            "columns": columns,
            "rows": rows,
            "count": len(rows),
        }
    except Exception as e:
        logger.error("Query failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Query error: {e}")
