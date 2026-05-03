"""User routes.

Provides user profile and subscription management endpoints.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tube_alpha.models import UserProfile
from tube_alpha.routers.dependencies import get_auth_service, get_user_service
from tube_alpha.services.auth import AuthService
from tube_alpha.services.users import UserService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["users"])


def _require_email(request: Request, auth: AuthService) -> str:
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return email


@router.get("/users/me", response_model=UserProfile)
async def get_current_user(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    """Get current user profile with subscription details."""
    email = _require_email(request, auth)
    profile = users.get_profile(email)
    return UserProfile(**profile)


@router.post("/users/subscribe", response_model=UserProfile)
async def activate_subscription(
    request: Request,
    duration_days: int = Query(30, ge=1, le=365, description="Subscription duration in days"),
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    """Activate or extend the current user's pro subscription."""
    email = _require_email(request, auth)
    profile = users.activate_subscription(email, duration_days=duration_days)
    logger.info("Subscription activated for %s (%d days)", email, duration_days)
    return UserProfile(**profile)


@router.post("/users/unsubscribe", response_model=UserProfile)
async def deactivate_subscription(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    """Deactivate the current user's pro subscription."""
    email = _require_email(request, auth)
    profile = users.deactivate_subscription(email)
    return UserProfile(**profile)


@router.post("/users/redeem", response_model=UserProfile)
async def redeem_promo_code(
    request: Request,
    code: str = Query(..., min_length=1, description="Promo code to redeem"),
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    """Redeem a promo code to activate or extend a pro subscription."""
    email = _require_email(request, auth)
    try:
        profile = users.redeem_promo_code(email, code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("Promo code redeemed by %s", email)
    return UserProfile(**profile)
