"""One-time dev setup: create DB schema and add dev@example.com as pro user."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tube_alpha.config import Settings
from tube_alpha.database import Database

def main():
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    for db_path in [settings.data_db_path, settings.admin_db_path]:
        db = Database(db_path)
        db.create_schema(settings.schema_file)
        db.close()
        print(f"Created schema: {db_path.name}")

    # Add dev user as pro
    from tube_alpha.services.users import UserService
    users = UserService(settings)
    users.activate_subscription("dev@example.com", duration_days=30)
    print("Added dev@example.com as pro user (30 days)")

    # Seed a test promo code
    from tube_alpha.database import Database
    admin_db = Database(settings.admin_db_path)
    admin_db.execute(
        "INSERT OR IGNORE INTO promo_codes (code, duration_days, max_uses) VALUES (?, ?, ?)",
        ("ALPHA1DAY", 1, 10),
    )
    print("Seeded promo code: ALPHA1DAY (1 day, max 10 uses)")

    print("\nReady. Run: python main.py")
    print("Then POST http://127.0.0.1:8000/api/v1/videos/process with body: {\"url\": \"https://youtube.com/watch?v=VIDEO_ID\"}")

if __name__ == "__main__":
    main()
