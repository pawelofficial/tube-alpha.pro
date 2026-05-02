"""Google OAuth2 sign-in flow.

/login      → redirects to Google's consent screen
/auth/callback → Google calls this back with ?code=; exchanges for email; sets session
/logout     → clears session

In development mode the whole flow is bypassed — /login redirects straight home
and AuthService returns a mock email without touching the session.
"""

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from tube_alpha.routers.dependencies import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/login")
async def login(request: Request, next: str = "/"):
    settings = get_settings()
    if settings.is_development:
        return RedirectResponse(next, status_code=302)

    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    request.session["next"] = next

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{_GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
):
    if error or not code:
        logger.warning("OAuth error from Google: %s", error)
        return RedirectResponse("/", status_code=302)

    if state != request.session.get("oauth_state"):
        logger.warning("OAuth state mismatch — possible CSRF")
        return RedirectResponse("/", status_code=302)

    settings = get_settings()

    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(_GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            })
            tokens = token_resp.json()

        access_token = tokens.get("access_token")
        if not access_token:
            logger.error("No access_token in Google response: %s", tokens)
            return RedirectResponse("/", status_code=302)

        async with httpx.AsyncClient() as client:
            userinfo_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo = userinfo_resp.json()

        email = userinfo.get("email")
        if email and userinfo.get("email_verified"):
            request.session["user_email"] = email
            logger.info("User signed in: %s", email)
        else:
            logger.warning("Google userinfo missing or unverified email: %s", userinfo)

    except Exception:
        logger.exception("OAuth callback failed")

    next_url = request.session.pop("next", "/")
    return RedirectResponse(next_url, status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)
