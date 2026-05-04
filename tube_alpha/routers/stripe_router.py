"""Stripe payment routes."""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from tube_alpha.routers.dependencies import get_auth_service, get_settings, get_user_service
from tube_alpha.services.auth import AuthService
from tube_alpha.services.users import UserService
from tube_alpha.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stripe"])


def _require_email(request: Request, auth: AuthService) -> str:
    email = auth.get_email_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return email


@router.post("/api/v1/stripe/checkout")
async def create_checkout_session(
    request: Request,
    type: str = "onetime",
    auth: AuthService = Depends(get_auth_service),
    users: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    """Create a Stripe Checkout Session and return the redirect URL.

    type='onetime'      → one-time payment (mode=payment)
    type='subscription' → recurring subscription (mode=subscription)
    """
    email = _require_email(request, auth)
    stripe.api_key = settings.stripe_secret_key

    if type == "subscription":
        price_id = settings.stripe_price_id_sub
        mode = "subscription"
    else:
        price_id = settings.stripe_price_id
        mode = "payment"

    if not price_id:
        raise HTTPException(status_code=400, detail=f"Price not configured for type '{type}'")

    base_url = str(request.base_url).rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            customer_email=email,
            line_items=[{"price": price_id, "quantity": 1}],
            mode=mode,
            success_url=f"{base_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/pricing",
            metadata={"email": email},
        )
    except stripe.error.StripeError as e:
        logger.error("Stripe checkout error: %s", e)
        raise HTTPException(status_code=400, detail=str(e.user_message or e))
    return {"url": session.url}


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    users: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    stripe.api_key = settings.stripe_secret_key

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id")
        email = session.get("customer_email") or session.get("metadata", {}).get("email")
        mode = session.get("mode") or "payment"
        if email and session_id:
            users.process_stripe_session(
                session_id=session_id,
                email=email,
                mode=mode,
                credits=settings.stripe_onetime_credits,
                days=settings.stripe_pro_days,
            )

    return {"status": "ok"}
