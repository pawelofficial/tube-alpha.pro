"""Data endpoints for frontend visualization.

Each endpoint answers a question the frontend wants to display.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from tube_alpha.routers.dependencies import get_data_service
from tube_alpha.services.data import DataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/data", tags=["data"])


@router.get("/video/{video_id}")
async def video_overview(
    video_id: str,
    data: DataService = Depends(get_data_service),
) -> Dict[str, Any]:
    """What does this video talk about?

    Returns video metadata + sentiment tiles for a single video.
    """
    return data.video_overview(video_id)


@router.get("/asset/{asset}")
async def asset_overview(
    asset: str,
    from_date: Optional[str] = Query(None, description="Filter: start date (ISO format, e.g. 2024-01-01)"),
    to_date: Optional[str] = Query(None, description="Filter: end date (ISO format)"),
    sentiment: Optional[str] = Query(None, description="Filter: sentiment direction (e.g. bullish, bearish)"),
    data: DataService = Depends(get_data_service),
) -> Dict[str, Any]:
    """What's the sentiment on this asset?

    Returns all mentions of the asset across videos with per-video breakdown.
    Supports optional time range and sentiment direction filters.
    """
    return data.asset_overview(asset, from_date=from_date, to_date=to_date, sentiment=sentiment)


@router.get("/guest/{guest}")
async def guest_overview(
    guest: str,
    from_date: Optional[str] = Query(None, description="Filter: start date (ISO format)"),
    to_date: Optional[str] = Query(None, description="Filter: end date (ISO format)"),
    sentiment: Optional[str] = Query(None, description="Filter: sentiment direction (e.g. bullish, bearish)"),
    data: DataService = Depends(get_data_service),
) -> Dict[str, Any]:
    """What has this guest/interviewee said?

    Returns all videos featuring this guest with aggregated sentiment tiles.
    Supports optional time range and sentiment direction filters.
    """
    return data.guest_overview(guest, from_date=from_date, to_date=to_date, sentiment=sentiment)


@router.get("/latest")
async def latest_overview(
    limit: int = Query(50, ge=1, le=500),
    from_date: Optional[str] = Query(None, description="Filter: start date (ISO format)"),
    to_date: Optional[str] = Query(None, description="Filter: end date (ISO format)"),
    sentiment: Optional[str] = Query(None, description="Filter: sentiment direction (e.g. bullish, bearish)"),
    data: DataService = Depends(get_data_service),
) -> Dict[str, Any]:
    """What's the latest across everything?

    Returns aggregated sentiment tiles from the most recent data.
    Supports optional time range and sentiment direction filters.
    """
    return data.latest_overview(limit=limit, from_date=from_date, to_date=to_date, sentiment=sentiment)


@router.get("/videos")
async def videos_list(
    limit: int = Query(50, ge=1, le=500),
    data: DataService = Depends(get_data_service),
) -> List[Dict]:
    """What videos have been processed?"""
    return data.videos_list(limit=limit)


@router.get("/assets")
async def assets_list(
    data: DataService = Depends(get_data_service),
) -> List[Dict]:
    """What assets are being tracked?

    Returns all unique assets with aggregate scores and mention counts.
    """
    return data.assets_list()


@router.get("/guests")
async def guests_list(
    data: DataService = Depends(get_data_service),
) -> List[Dict]:
    """What guests have been interviewed?"""
    return data.guests_list()
