"""Page routes — serves Jinja2 HTML templates."""

import logging
from pathlib import Path

import stripe
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tube_alpha.config import Settings
from tube_alpha.routers.dependencies import get_auth_service, get_settings, get_user_service
from tube_alpha.services.auth import AuthService
from tube_alpha.services.users import UserService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
try:
    _STATIC_VERSION = str(int((_STATIC_DIR / "styles.css").stat().st_mtime))
except OSError:
    _STATIC_VERSION = "0"


def _base_ctx(request: Request, auth: AuthService, users: UserService) -> dict:
    """Common template context for every page.

    In development mode AuthService returns a fixed dev email so the UI
    always shows the user as logged in locally.  In production it decodes
    the Azure AD B2C header.
    """
    email = auth.get_email_from_request(request)
    return {
        "request": request,
        "is_authenticated": email is not None,
        "pro": users.is_pro(email),
        "user_email": email,
        "static_v": _STATIC_VERSION,
    }


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    return templates.TemplateResponse("home.html", _base_ctx(request, auth, users))


@router.get("/profile", response_class=HTMLResponse)
async def profile(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    ctx = _base_ctx(request, auth, users)
    if not ctx["user_email"]:
        return RedirectResponse(url="/login?next=/profile", status_code=302)
    p = users.get_profile(ctx["user_email"])
    ctx["plan_type"] = p["plan_type"]
    ctx["videos_remaining"] = p["videos_remaining"]
    ctx["pro_end"] = p["pro_end"]
    ctx["pro_days_remaining"] = p["pro_days_remaining"]
    return templates.TemplateResponse("profile.html", ctx)


@router.get("/yourvid", response_class=HTMLResponse)
async def yourvid(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    return templates.TemplateResponse("yourvid.html", _base_ctx(request, auth, users))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    return templates.TemplateResponse("dashboard.html", _base_ctx(request, auth, users))


@router.get("/about", response_class=HTMLResponse)
async def about(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    return templates.TemplateResponse("about.html", _base_ctx(request, auth, users))


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
):
    return templates.TemplateResponse("pricing.html", _base_ctx(request, auth, users))


@router.get("/payment/success", response_class=HTMLResponse)
async def payment_success(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    session_id = request.query_params.get("session_id")
    auth_email = auth.get_email_from_request(request)

    if session_id and settings.stripe_secret_key:
        try:
            stripe.api_key = settings.stripe_secret_key
            stripe_session = stripe.checkout.Session.retrieve(session_id)
            # Prefer the email Stripe recorded for this payment over whoever
            # happens to be logged in right now; fall back to the auth email
            # only if Stripe has nothing (shouldn't happen in practice).
            stripe_email = stripe_session.customer_email or (
                (stripe_session.metadata or {}).get("email")
            )
            email = stripe_email or auth_email
            if email and stripe_session.payment_status in ("paid", "no_payment_required"):
                users.process_stripe_session(
                    session_id=session_id,
                    email=email,
                    mode=stripe_session.mode,
                    credits=settings.stripe_onetime_credits,
                    days=settings.stripe_pro_days,
                )
        except Exception:
            logger.exception("Failed to verify Stripe session %s on success page", session_id)

    return templates.TemplateResponse("payment_success.html", _base_ctx(request, auth, users))
