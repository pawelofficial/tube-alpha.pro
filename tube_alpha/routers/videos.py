"""Video processing routes.

Thin HTTP wrapper over VideoPipeline service.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
import re

from tube_alpha.models import VideoProcessResponse
from tube_alpha.routers.dependencies import get_auth_service, get_pipeline, get_user_service
from tube_alpha.services.auth import AuthService
from tube_alpha.services.pipeline import VideoPipeline
from tube_alpha.services.users import UserService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["videos"])


class VideoProcessInput(BaseModel):
    url: str = Field(..., min_length=1, description="YouTube URL")
    video_id: Optional[str] = Field(None, description="Pre-extracted video ID")

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        patterns = [
            r"^(https?://)?(www\.)?(youtube\.com/(watch\?v=|embed/|v/)|youtu\.be/|m\.youtube\.com/watch\?v=)[\w-]{11}",
            r"^(https?://)?(www\.)?youtube\.com/shorts/[\w-]{11}",
        ]
        if not any(re.match(p, v) for p in patterns):
            raise ValueError("Invalid YouTube URL format")
        return v


@router.post("/videos/process", response_model=VideoProcessResponse)
async def process_video(
    body: VideoProcessInput,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
    pipeline: VideoPipeline = Depends(get_pipeline),
):
    """Process a YouTube video: fetch transcript, extract sentiments.

    Requires authentication and pro subscription.
    """
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not users.is_pro(email):
        raise HTTPException(status_code=403, detail="Pro subscription required")

    try:
        result = pipeline.process_video(url=body.url, video_id=body.video_id)
        return result
    except Exception as e:
        logger.exception("Video processing failed for %s", body.url)
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")
