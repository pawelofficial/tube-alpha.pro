"""Health check endpoint."""

from fastapi import APIRouter, Depends

from tube_alpha.config import Settings
from tube_alpha.models import HealthResponse
from tube_alpha.routers.dependencies import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)):
    return HealthResponse(status="ok", environment=settings.environment)
