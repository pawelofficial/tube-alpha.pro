"""Authentication routes.

Handles login/logout redirects for Azure AD B2C.
In development mode, redirects to home instead.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from tube_alpha.config import Settings
from tube_alpha.routers.dependencies import get_settings

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login(next: str = "/", settings: Settings = Depends(get_settings)):
    if settings.is_development:
        return RedirectResponse(next, status_code=302)
    encoded_next = next.replace("/", "%2F")
    return RedirectResponse(
        f"/.auth/login/aad?post_login_redirect_uri={encoded_next}",
        status_code=302,
    )


@router.get("/logout")
async def logout(settings: Settings = Depends(get_settings)):
    if settings.is_development:
        return RedirectResponse("/", status_code=302)
    return RedirectResponse(
        "/.auth/logout?post_logout_redirect_uri=%2F",
        status_code=302,
    )
