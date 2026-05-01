"""Authentication service.

In development mode returns a mock email without touching the session.
In production reads the user email from the session set by the Google OAuth2 flow.
"""

import logging
from typing import Optional

from tube_alpha.config import Settings

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def get_email_from_request(self, request) -> Optional[str]:
        if self._settings.is_development:
            return "dev@example.com"
        return request.session.get("user_email")
