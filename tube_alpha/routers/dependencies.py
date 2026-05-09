"""FastAPI dependency injection.

Provides shared service instances via FastAPI's Depends() mechanism.
"""

import os
from functools import lru_cache

from fastapi import Header, HTTPException

from tube_alpha.config import Settings
from tube_alpha.services.auth import AuthService
from tube_alpha.services.pipeline import VideoPipeline
from tube_alpha.services.sentiment import SentimentService
from tube_alpha.services.users import UserService
from tube_alpha.services.data import DataService
from tube_alpha.services.scheduler import SchedulerService
from tube_alpha.services.youtube import YouTubeService


def require_admin_key(x_admin_key: str = Header(...)) -> None:
    secret = os.environ.get("ADMIN_SECRET_KEY", "")
    if not secret:
        raise HTTPException(status_code=503, detail="ADMIN_SECRET_KEY not configured")
    if x_admin_key != secret:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


@lru_cache()
def get_auth_service() -> AuthService:
    return AuthService(get_settings())


@lru_cache()
def get_user_service() -> UserService:
    return UserService(get_settings())


@lru_cache()
def get_youtube_service() -> YouTubeService:
    return YouTubeService(get_settings())


@lru_cache()
def get_sentiment_service() -> SentimentService:
    return SentimentService(get_settings())


@lru_cache()
def get_pipeline() -> VideoPipeline:
    return VideoPipeline(get_settings())


@lru_cache()
def get_data_service() -> DataService:
    return DataService(get_settings())


@lru_cache()
def get_scheduler() -> SchedulerService:
    return SchedulerService(get_settings())
