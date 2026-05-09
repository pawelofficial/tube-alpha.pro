"""User management service.

Two pro access models coexist:
  subscription — time-based (subscription_active=1, end_pro set)
  onetime      — credit-based (videos_remaining > 0)

is_pro() returns True if either model grants access.
consume_video_credit() is a no-op for subscription users.
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
        self._ensure_users_table()
        self._ensure_promo_table()
        self._ensure_sessions_table()
        self._migrate_users_table()

    def _ensure_users_table(self) -> None:
        self._db.conn.execute(
            "CREATE TABLE IF NOT EXISTS users("
            "no int, email varchar, subscription_active boolean, "
            "start_pro timestamp, end_pro timestamp, "
            "plan_type varchar default 'free', "
            "videos_remaining integer default 0)"
        )
        self._db.conn.commit()

    def _ensure_sessions_table(self) -> None:
        self._db.conn.execute(
            "CREATE TABLE IF NOT EXISTS stripe_sessions("
            "session_id varchar primary key, email varchar, mode varchar, "
            "processed_at datetime default current_timestamp)"
        )
        self._db.conn.commit()

    def _ensure_promo_table(self) -> None:
        self._db.conn.execute(
            "CREATE TABLE IF NOT EXISTS promo_codes("
            "code varchar primary key, duration_days int, max_uses int, "
            "uses_count int default 0, active boolean default 1, "
            "created_at datetime default current_timestamp)"
        )
        self._db.conn.commit()

    def _migrate_users_table(self) -> None:
        """Add new columns to users table if missing (safe on existing DBs)."""
        existing = set(
            self._db.fetch_scalars("SELECT name FROM pragma_table_info('users')")
        )
        migrations = [
            ("plan_type", "ALTER TABLE users ADD COLUMN plan_type VARCHAR DEFAULT 'free'"),
            ("videos_remaining", "ALTER TABLE users ADD COLUMN videos_remaining INTEGER DEFAULT 0"),
        ]
        for col, sql in migrations:
            if col not in existing:
                self._db.execute(sql)
                logger.info("DB migration: added column users.%s", col)

        # Rename pro_active → subscription_active
        if "pro_active" in existing and "subscription_active" not in existing:
            self._db.conn.execute("ALTER TABLE users RENAME COLUMN pro_active TO subscription_active")
            self._db.conn.commit()
            logger.info("DB migration: renamed column users.pro_active → subscription_active")

    def _ensure_user(self, email: str) -> None:
        if self._db.fetch_scalar("SELECT 1 FROM users WHERE email = ?", (email,)) is None:
            self._db.execute(
                "INSERT INTO users (email, subscription_active, plan_type, videos_remaining) "
                "VALUES (?, 0, 'free', 0)",
                (email,),
            )

    # ------------------------------------------------------------------
    # Pro status
    # ------------------------------------------------------------------

    def is_pro(self, email: Optional[str]) -> bool:
        """True if user has an active subscription OR remaining one-time credits."""
        if not email:
            return False

        row = self._db.fetch_one(
            "SELECT subscription_active, end_pro, videos_remaining FROM users WHERE email = ?",
            (email,),
        )
        if not row:
            return False

        # Subscription path: subscription_active must be 1 AND end_pro must be in the future
        if row["subscription_active"]:
            end_pro = row.get("end_pro")
            if not end_pro:
                return True  # legacy rows with no end date — treat as active
            try:
                if date.fromisoformat(str(end_pro)) >= date.today():
                    return True
            except (ValueError, TypeError):
                pass

        # One-time path: credits remaining
        if (row.get("videos_remaining") or 0) > 0:
            return True

        return False

    def _effective_plan_type(self, row: dict) -> str:
        """Derive display plan type from actual DB state (not the stored column)."""
        end_pro = row.get("end_pro")
        if row.get("subscription_active"):
            try:
                if not end_pro or date.fromisoformat(str(end_pro)) >= date.today():
                    return "subscription"
            except (ValueError, TypeError):
                pass
        if (row.get("videos_remaining") or 0) > 0:
            return "onetime"
        return "free"

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, email: str) -> dict:
        row = self._db.fetch_one(
            "SELECT email, subscription_active, start_pro, end_pro, plan_type, videos_remaining "
            "FROM users WHERE email = ?",
            (email,),
        )
        if not row:
            return {
                "email": email,
                "is_pro": False,
                "plan_type": "free",
                "pro_start": None,
                "pro_end": None,
                "pro_days_remaining": None,
                "videos_remaining": 0,
            }

        is_pro = self.is_pro(email)
        pro_end = row.get("end_pro")
        days_remaining = None
        if pro_end:
            try:
                end_date = date.fromisoformat(str(pro_end))
                days_remaining = max(0, (end_date - date.today()).days)
            except (ValueError, TypeError):
                pass

        return {
            "email": row["email"],
            "is_pro": is_pro,
            "plan_type": self._effective_plan_type(row),
            "pro_start": row.get("start_pro"),
            "pro_end": pro_end,
            "pro_days_remaining": days_remaining,
            "videos_remaining": row.get("videos_remaining") or 0,
        }

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate_subscription(self, email: str, duration_days: int = 30) -> dict:
        """Activate or extend a time-based pro subscription."""
        self._ensure_user(email)
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=duration_days)).isoformat()

        row = self._db.fetch_one(
            "SELECT subscription_active, end_pro FROM users WHERE email = ?", (email,)
        )
        if row and row["subscription_active"] and row.get("end_pro"):
            try:
                current_end = date.fromisoformat(str(row["end_pro"]))
                if current_end > date.today():
                    end = (current_end + timedelta(days=duration_days)).isoformat()
            except (ValueError, TypeError):
                pass

        self._db.execute(
            "UPDATE users SET subscription_active = 1, plan_type = 'subscription', "
            "start_pro = ?, end_pro = ? WHERE email = ?",
            (start, end, email),
        )
        logger.info("Subscription activated for %s until %s", email, end)
        return self.get_profile(email)

    def activate_onetime(self, email: str, credits: int = 10) -> dict:
        """Add one-time video analysis credits. Stacks on existing credits."""
        self._ensure_user(email)
        self._db.execute(
            "UPDATE users SET plan_type = 'onetime', start_pro = ?, "
            "videos_remaining = videos_remaining + ? WHERE email = ?",
            (date.today().isoformat(), credits, email),
        )
        logger.info("One-time credits added for %s: +%d", email, credits)
        return self.get_profile(email)

    def process_stripe_session(
        self,
        session_id: str,
        email: str,
        mode: str,
        credits: int = 10,
        days: int = 30,
    ) -> bool:
        """Idempotently activate pro access for a completed Stripe session.

        Uses stripe_sessions as a deduplication key so calling this from both
        the success page redirect and the webhook never double-activates.
        Returns True if newly activated, False if already processed.
        """
        if self._db.fetch_scalar(
            "SELECT 1 FROM stripe_sessions WHERE session_id = ?", (session_id,)
        ):
            logger.info("Stripe session %s already processed — skipping", session_id)
            return False

        if mode == "subscription":
            self.activate_subscription(email, duration_days=days)
        else:
            self.activate_onetime(email, credits=credits)

        self._db.execute(
            "INSERT OR IGNORE INTO stripe_sessions (session_id, email, mode) VALUES (?, ?, ?)",
            (session_id, email, mode),
        )
        logger.info("Stripe session %s processed for %s (mode=%s)", session_id, email, mode)
        return True

    def deactivate_subscription(self, email: str) -> dict:
        self._db.execute(
            "UPDATE users SET subscription_active = 0 WHERE email = ?", (email,)
        )
        logger.info("Subscription deactivated for %s", email)
        return self.get_profile(email)

    # ------------------------------------------------------------------
    # Credit consumption
    # ------------------------------------------------------------------

    def consume_video_credit(self, email: str) -> bool:
        """Atomically consume 1 credit for one-time users.

        No-op for active subscription users (subscription takes priority).
        Returns True if a credit was consumed.
        """
        cursor = self._db.execute(
            "UPDATE users SET videos_remaining = videos_remaining - 1 "
            "WHERE email = ? AND videos_remaining > 0 "
            "AND NOT (subscription_active = 1 AND end_pro >= date('now'))",
            (email,),
        )
        consumed = cursor.rowcount > 0
        if consumed:
            logger.info("Video credit consumed for %s", email)
        return consumed

    # ------------------------------------------------------------------
    # Promo codes
    # ------------------------------------------------------------------

    def redeem_promo_code(self, email: str, code: str) -> dict:
        """Redeem a promo code to activate/extend a subscription.

        Raises ValueError on invalid/exhausted codes.
        """
        row = self._db.fetch_one(
            "SELECT duration_days, max_uses, uses_count, active FROM promo_codes WHERE code = ?",
            (code,),
        )
        if row is None:
            raise ValueError("Invalid promo code")
        if not row["active"]:
            raise ValueError("This promo code is no longer active")
        if row["max_uses"] is not None and row["uses_count"] >= row["max_uses"]:
            raise ValueError("This promo code has already been fully redeemed")

        self._db.execute(
            "UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code = ?",
            (code,),
        )
        logger.info("Promo code %s redeemed by %s (%d days)", code, email, row["duration_days"])
        return self.activate_subscription(email, duration_days=row["duration_days"])
