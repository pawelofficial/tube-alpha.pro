"""Page routes — serves Jinja2 HTML templates."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from tube_alpha.routers.dependencies import get_auth_service, get_user_service
from tube_alpha.services.auth import AuthService
from tube_alpha.services.users import UserService

router = APIRouter(tags=["pages"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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
    return templates.TemplateResponse("profile.html", _base_ctx(request, auth, users))


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
