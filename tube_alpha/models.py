"""Pydantic models for request/response schemas.

These models define the API contract and can be used for validation
both in HTTP endpoints and programmatic usage.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# --- Request Models ---


class VideoProcessRequest(BaseModel):
    """Request to process a YouTube video."""

    url: str = Field(..., min_length=1, description="YouTube video URL")
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

    @field_validator("video_id")
    @classmethod
    def validate_video_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if len(v) != 11:
                raise ValueError("Invalid video ID length")
            valid = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
            if not all(c in valid for c in v):
                raise ValueError("Invalid video ID format")
        return v


class SQLQueryRequest(BaseModel):
    """Request to execute a read-only SQL query."""

    sql: str = Field(..., min_length=1)

    @field_validator("sql")
    @classmethod
    def must_be_select(cls, v: str) -> str:
        if not v.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        return v


# --- Response Models ---


class SentimentItem(BaseModel):
    """A single sentiment extraction from a video."""

    asset: str
    sentiment: str
    quotes: List[str]
    video_id: str
    ts: Optional[str] = None


class SentimentTile(BaseModel):
    """Aggregated sentiment tile for display."""

    asset: str
    avg: float
    sentiments: List[str]
    quotes: List[str]
    videos: List[str]
    last_ts: Optional[str] = None


class VideoProcessResponse(BaseModel):
    """Response from video processing."""

    success: bool
    video_id: str
    standard_url: str
    summary: str
    tiles: List[SentimentTile] = []
    message: str = ""


class VideoMetadata(BaseModel):
    """YouTube video metadata."""

    video_id: str
    title: str
    description: str
    guest: Optional[str] = None
    is_investment_related: bool = False
    description_summary: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    environment: str = ""


class UserProfile(BaseModel):
    """User profile information."""

    email: str
    is_pro: bool = False
    plan_type: str = "free"
    pro_start: Optional[str] = None
    pro_end: Optional[str] = None
    pro_days_remaining: Optional[int] = None
    videos_remaining: int = 0
