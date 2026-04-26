"""User management service.

Handles user profile data and subscription status.

Usage:
    from tube_alpha.services.users import UserService
    from tube_alpha.config import Settings

    service = UserService(Settings())
    is_pro = service.is_pro("user@example.com")

    # Subscription management
    service.activate_subscription("user@example.com", duration_days=30)
    service.deactivate_subscription("user@example.com")
"""

import logging
from datetime import date, timedelta
from typing import Optional

from tube_alpha.config import Settings
from tube_alpha.database import Database

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._db = Database(settings.admin_db_path)

    def _ensure_user(self, email: str) -> None:
        """Create user row if it doesn't exist."""
        existing = self._db.fetch_scalar(
            "SELECT 1 FROM users WHERE email = ?", (email,)
        )
        if existing is None:
            self._db.execute(
                "INSERT INTO users (email, pro_active) VALUES (?, 0)", (email,)
            )

    def is_pro(self, email: Optional[str]) -> bool:
        """Check if user has an active pro subscription."""
        if not email:
            return False

        result = self._db.fetch_scalar(
            "SELECT 1 FROM users WHERE email = ? AND pro_active = 1",
            (email,),
        )
        return result is not None

    def get_profile(self, email: str) -> dict:
        """Get user profile with subscription details."""
        row = self._db.fetch_one(
            "SELECT email, pro_active, start_pro, end_pro FROM users WHERE email = ?",
            (email,),
        )
        if row:
            is_pro = bool(row["pro_active"])
            pro_start = row.get("start_pro")
            pro_end = row.get("end_pro")
            days_remaining = None
            if is_pro and pro_end:
                try:
                    end_date = date.fromisoformat(pro_end)
                    days_remaining = max(0, (end_date - date.today()).days)
                except ValueError:
                    pass
            return {
                "email": row["email"],
                "is_pro": is_pro,
                "pro_start": pro_start,
                "pro_end": pro_end,
                "pro_days_remaining": days_remaining,
            }
        return {"email": email, "is_pro": False}

    def activate_subscription(self, email: str, duration_days: int = 30) -> dict:
        """Activate or extend a user's pro subscription.

        Returns the updated profile.
        """
        self._ensure_user(email)
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=duration_days)).isoformat()

        # If user already has an active subscription with time left, extend from end_pro
        row = self._db.fetch_one(
            "SELECT pro_active, end_pro FROM users WHERE email = ?", (email,)
        )
        if row and row["pro_active"] and row.get("end_pro"):
            try:
                current_end = date.fromisoformat(row["end_pro"])
                if current_end > date.today():
                    end = (current_end + timedelta(days=duration_days)).isoformat()
            except ValueError:
                pass

        self._db.execute(
            "UPDATE users SET pro_active = 1, start_pro = ?, end_pro = ? WHERE email = ?",
            (start, end, email),
        )
        logger.info("Subscription activated for %s until %s", email, end)
        return self.get_profile(email)

    def deactivate_subscription(self, email: str) -> dict:
        """Deactivate a user's pro subscription.

        Returns the updated profile.
        """
        self._db.execute(
            "UPDATE users SET pro_active = 0 WHERE email = ?", (email,)
        )
        logger.info("Subscription deactivated for %s", email)
        return self.get_profile(email)
