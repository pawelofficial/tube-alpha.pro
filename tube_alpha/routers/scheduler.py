"""Scheduler control routes.

Start/stop/trigger the background channel scraping scheduler.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tube_alpha.routers.dependencies import get_auth_service, get_scheduler
from tube_alpha.services.auth import AuthService
from tube_alpha.services.scheduler import SchedulerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


@router.get("/status")
async def scheduler_status(
    scheduler: SchedulerService = Depends(get_scheduler),
) -> Dict[str, Any]:
    """Get current scheduler status."""
    return scheduler.status


@router.post("/start")
async def start_scheduler(
    request: Request,
    interval_hours: int = Query(6, ge=1, le=168, description="Hours between scrapes"),
    auth: AuthService = Depends(get_auth_service),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> Dict[str, Any]:
    """Start the background scraping scheduler. Requires authentication."""
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")

    if scheduler.is_running:
        return {"message": "Scheduler is already running", **scheduler.status}

    scheduler.start(interval_hours=interval_hours)
    logger.info("Scheduler started by %s with %dh interval", email, interval_hours)
    return {"message": "Scheduler started", **scheduler.status}


@router.post("/stop")
async def stop_scheduler(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> Dict[str, Any]:
    """Stop the background scraping scheduler. Requires authentication."""
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")

    scheduler.stop()
    logger.info("Scheduler stopped by %s", email)
    return {"message": "Scheduler stopped", **scheduler.status}


@router.post("/run-now")
async def run_now(
    request: Request,
    max_videos: Optional[int] = Query(None, ge=1, le=100, description="Max videos per channel (default from config)"),
    auth: AuthService = Depends(get_auth_service),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> Dict[str, Any]:
    """Trigger an immediate scrape of all channels. Requires authentication."""
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")

    logger.info("Manual scrape triggered by %s (max_videos=%s)", email, max_videos)
    results = scheduler.run_once(max_videos=max_videos)
    return {"message": "Scrape complete", "results": results}
