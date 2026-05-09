"""Scheduler control routes.

Start/stop/trigger the background channel scraping scheduler.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from tube_alpha.routers.dependencies import get_scheduler, require_admin_key
from tube_alpha.services.scheduler import SchedulerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


@router.get("/status")
async def scheduler_status(
    scheduler: SchedulerService = Depends(get_scheduler),
    _: None = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Get current scheduler status."""
    return scheduler.status


@router.post("/start")
async def start_scheduler(
    interval_hours: int = Query(6, ge=1, le=168, description="Hours between scrapes"),
    scheduler: SchedulerService = Depends(get_scheduler),
    _: None = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Start the background scraping scheduler."""
    if scheduler.is_running:
        return {"message": "Scheduler is already running", **scheduler.status}

    scheduler.start(interval_hours=interval_hours)
    logger.info("Scheduler started with %dh interval", interval_hours)
    return {"message": "Scheduler started", **scheduler.status}


@router.post("/stop")
async def stop_scheduler(
    scheduler: SchedulerService = Depends(get_scheduler),
    _: None = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Stop the background scraping scheduler."""
    scheduler.stop()
    logger.info("Scheduler stopped")
    return {"message": "Scheduler stopped", **scheduler.status}


@router.post("/run-now")
async def run_now(
    max_videos: Optional[int] = Query(None, ge=1, le=100, description="Max videos per channel (default from config)"),
    scheduler: SchedulerService = Depends(get_scheduler),
    _: None = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Trigger an immediate scrape of all channels."""
    import asyncio
    import functools
    logger.info("Manual scrape triggered (max_videos=%s)", max_videos)
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, functools.partial(scheduler.run_once, max_videos=max_videos))
    return {"message": "Scrape complete", "results": results}
