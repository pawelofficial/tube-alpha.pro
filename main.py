"""Tube Alpha - YouTube investment sentiment analysis.

Entry point for the FastAPI application.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Load environment variables before importing anything else
load_dotenv()

from tube_alpha.routers import auth, data, health, pages, scheduler, sentiments, users, videos
from tube_alpha.routers.dependencies import get_scheduler

# Configure logging
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "core" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "ai.log"

log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logging.basicConfig(
    level=log_level,
    format=log_format,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the app."""
    # Startup: optionally auto-start the scheduler
    sched = get_scheduler()
    auto_scrape = os.getenv("AUTO_SCRAPE_ENABLED", "").lower() in ("1", "true", "yes")
    if auto_scrape:
        interval = int(os.getenv("AUTO_SCRAPE_INTERVAL_HOURS", "6"))
        sched.start(interval_hours=interval)
        logger.info("Auto-scrape scheduler started (%dh interval)", interval)

    yield

    # Shutdown: stop scheduler if running
    if sched.is_running:
        sched.stop()
        logger.info("Scheduler stopped on shutdown")


app = FastAPI(
    title="Tube Alpha",
    description="YouTube investment sentiment analysis API",
    version="1.0.0",
    lifespan=lifespan,
)

if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# --- Page routes (HTML) ---
app.include_router(pages.router)

# --- API routes ---
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(videos.router)
app.include_router(sentiments.router)
app.include_router(users.router)
app.include_router(data.router)
app.include_router(scheduler.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
