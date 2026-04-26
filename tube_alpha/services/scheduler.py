"""Scheduled channel scraping service.

Runs periodic scraping of configured YouTube channels as a background task.
Integrates with FastAPI's lifespan events — starts on app boot, stops on shutdown.

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
from tube_alpha.services.pipeline import VideoPipeline

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pipeline = VideoPipeline(settings)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._interval_hours = 6  # default: scrape every 6 hours
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[Dict] = None

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

        self._last_run = datetime.now()
        self._last_result = results
        return results

    async def _loop(self):
        """Background loop that runs scraping on an interval."""
        logger.info(
            "Scheduler started: scraping %d channels every %d hours",
            len(self._settings.yt_channels),
            self._interval_hours,
        )
        while self._running:
            try:
                # Run scraping in a thread to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.run_once)
            except Exception as e:
                logger.error("Scheduler iteration failed: %s", e)

            # Sleep in small increments so we can stop quickly
            for _ in range(self._interval_hours * 3600):
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
