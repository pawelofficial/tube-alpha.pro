"""Sentiment data routes.

Provides endpoints to retrieve sentiment analysis results.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from tube_alpha.models import SentimentItem, SentimentTile
from tube_alpha.routers.dependencies import get_pipeline, get_sentiment_service
from tube_alpha.services.pipeline import VideoPipeline
from tube_alpha.services.sentiment import SentimentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["sentiments"])


@router.get("/sentiments", response_model=List[SentimentItem])
async def list_sentiments(
    video_id: Optional[str] = Query(None, description="Filter by video ID"),
    limit: int = Query(50, ge=1, le=500),
    sentiment_svc: SentimentService = Depends(get_sentiment_service),
):
    """Get raw sentiment data, optionally filtered by video."""
    return sentiment_svc.get_sentiments(video_id=video_id, limit=limit)


@router.get("/sentiments/tiles", response_model=List[SentimentTile])
async def get_tiles(
    video_id: Optional[str] = Query(None, description="Filter by video ID"),
    limit: int = Query(50, ge=1, le=500),
    pipeline: VideoPipeline = Depends(get_pipeline),
):
    """Get aggregated sentiment tiles for display."""
    if video_id:
        return pipeline.get_video_sentiment_tiles(video_id)
    return pipeline.get_all_sentiment_tiles(limit=limit)
