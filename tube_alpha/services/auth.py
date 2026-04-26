"""Authentication service.

Extracts user identity from Azure AD B2C headers.
In development mode, returns a mock user.

Usage:
    from tube_alpha.services.auth import AuthService
    from tube_alpha.config import Settings

    auth = AuthService(Settings())
    email = auth.get_email(headers={"X-MS-CLIENT-PRINCIPAL": "..."})
"""

import base64
import json
import logging
from typing import Dict, Optional

from tube_alpha.config import Settings

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._dev_email = "dev@example.com"

    def get_email(self, headers: Dict[str, str]) -> Optional[str]:
        """Extract user email from request headers.

        In development mode, returns a mock email.
        In production, decodes Azure AD B2C X-MS-CLIENT-PRINCIPAL header.
        """
        if self._settings.is_development:
            return self._dev_email

        b64 = headers.get("X-MS-CLIENT-PRINCIPAL") or headers.get("x-ms-client-principal")
        if not b64:
            return None

        # Pad base64 if needed
        b64 += "=" * (-len(b64) % 4)
        try:
            data = json.loads(base64.b64decode(b64))
            claims = {
                c["typ"].split("/")[-1]: c["val"]
                for c in data.get("claims", [])
            }
            email = (
                claims.get("preferred_username")
                or claims.get("emails")
                or claims.get("upn")
            )
            return email
        except Exception as e:
            logger.error("Error parsing user principal: %s", e)
            return None

    def get_email_from_request(self, request) -> Optional[str]:
        """Convenience: extract email from a FastAPI Request object."""
        return self.get_email(dict(request.headers))
