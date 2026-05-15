"""Scheduled channel scraping service.

Runs periodic scraping of configured YouTube channels as a background task.
Integrates with FastAPI's lifespan events — starts on app boot, stops on shutdown.

Persistence note:
    Cloud Run instances are killed when idle, taking the in-process asyncio
    timer with them. To prevent "no traffic = no scrapes", the last scrape
    timestamp is stored in ``data.sqlite`` (table ``scheduler_state``), which
    Litestream replicates to GCS. On every container boot the scheduler
    consults that timestamp: if more than ``interval_hours`` have passed,
    it scrapes immediately; otherwise it sleeps only the remaining time.

Usage (standalone):
    from tube_alpha.services.scheduler import SchedulerService
    from tube_alpha.config import Settings

    scheduler = SchedulerService(Settings())
    scheduler.run_once()  # Scrape all channels right now

Usage (with FastAPI):
    # Automatically started via lifespan in main.py
    # Or control via API:
    #   POST /api/v1/scheduler/start
    #   POST /api/v1/scheduler/stop
    #   POST /api/v1/scheduler/run-now
    #   GET  /api/v1/scheduler/status
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from tube_alpha.config import Settings
from tube_alpha.database import Database
from tube_alpha.services.pipeline import VideoPipeline

logger = logging.getLogger(__name__)

_LAST_SCRAPE_KEY = "last_scrape_at"


class SchedulerService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pipeline = VideoPipeline(settings)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._interval_hours = 6  # default: scrape every 6 hours
        self._last_result: Optional[Dict] = None
        self._db: Database = Database(settings.data_db_path)
        self._ensure_state_table()
        # Recover the last scrape timestamp from persistent storage so a
        # cold-started instance doesn't re-scrape if one happened recently.
        self._last_run: Optional[datetime] = self._load_last_run()

    # ------------------------------------------------------------------
    # Persistence helpers (the "flatfile" — backed by SQLite + Litestream)
    # ------------------------------------------------------------------
    def _ensure_state_table(self) -> None:
        """Defensive: create the table on prod DBs that pre-date this feature."""
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS scheduler_state "
                "(key VARCHAR PRIMARY KEY, value VARCHAR)"
            )
        except Exception as e:
            logger.warning("Could not ensure scheduler_state table exists: %s", e)

    def _load_last_run(self) -> Optional[datetime]:
        try:
            value = self._db.fetch_scalar(
                "SELECT value FROM scheduler_state WHERE key = ?",
                (_LAST_SCRAPE_KEY,),
            )
            if value:
                ts = datetime.fromisoformat(value)
                logger.info("Recovered last scrape timestamp from DB: %s", ts.isoformat())
                return ts
        except Exception as e:
            logger.warning("Could not load last scrape timestamp: %s", e)
        return None

    def _save_last_run(self, when: datetime) -> None:
        try:
            self._db.execute(
                "INSERT INTO scheduler_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_LAST_SCRAPE_KEY, when.isoformat()),
            )
        except Exception as e:
            logger.warning("Could not persist last scrape timestamp: %s", e)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def status(self) -> Dict:
        return {
            "running": self.is_running,
            "interval_hours": self._interval_hours,
            "channels": self._settings.yt_channels,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_result": self._last_result,
            "next_run": (self._last_run + timedelta(hours=self._interval_hours)).isoformat()
            if self._last_run and self.is_running
            else None,
        }

    # ------------------------------------------------------------------
    # Scrape
    # ------------------------------------------------------------------
    def run_once(self, max_videos: Optional[int] = None) -> Dict[str, Dict[str, int]]:
        """Scrape all configured channels once (blocking).

        Args:
            max_videos: Number of recent videos per channel. Defaults to
                        settings.yt_vids_count (config.yaml VIDS_COUNT).

        Returns dict of {channel_name: {success: N, failed: N}}.
        """
        results = {}
        for channel in self._settings.yt_channels:
            logger.info("Scheduled scrape starting for channel: %s", channel)
            try:
                stats = self._pipeline.scrape_and_process_channel(channel, max_videos=max_videos)
                results[channel] = stats
                logger.info("Scheduled scrape complete for %s: %s", channel, stats)
            except Exception as e:
                logger.error("Scheduled scrape failed for %s: %s", channel, e)
                results[channel] = {"success": 0, "failed": 0, "error": str(e)}

        now = datetime.now()
        self._last_run = now
        self._last_result = results
        self._save_last_run(now)
        return results

    def _seconds_until_due(self) -> int:
        """How long to wait before the next scrape is due.

        Returns 0 if a scrape is overdue (or has never happened).
        """
        if self._last_run is None:
            return 0
        elapsed = (datetime.now() - self._last_run).total_seconds()
        remaining = self._interval_hours * 3600 - elapsed
        return max(0, int(remaining))

    async def _loop(self):
        """Background loop that runs scraping on an interval, respecting
        the persisted ``last_scrape_at`` timestamp so cold-start instances
        don't re-scrape unnecessarily and idle-killed instances catch up."""
        logger.info(
            "Scheduler started: scraping %d channels every %d hours (last run: %s)",
            len(self._settings.yt_channels),
            self._interval_hours,
            self._last_run.isoformat() if self._last_run else "never",
        )
        while self._running:
            wait = self._seconds_until_due()
            if wait == 0:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.run_once)
                except Exception as e:
                    logger.error("Scheduler iteration failed: %s", e)
                wait = self._interval_hours * 3600
            else:
                logger.info(
                    "Last scrape was %.1fh ago — sleeping %.1fh until next due",
                    (self._interval_hours * 3600 - wait) / 3600,
                    wait / 3600,
                )

            # Sleep in 1s increments so we can stop quickly.
            for _ in range(wait):
                if not self._running:
                    break
                await asyncio.sleep(1)

        logger.info("Scheduler stopped")

    def start(self, interval_hours: int = 6):
        """Start the background scraping loop."""
        if self.is_running:
            logger.warning("Scheduler is already running")
            return

        self._interval_hours = interval_hours
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        logger.info("Scheduler started with %dh interval", interval_hours)

    def stop(self):
        """Stop the background scraping loop."""
        if not self.is_running:
            logger.warning("Scheduler is not running")
            return

        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Scheduler stop requested")
