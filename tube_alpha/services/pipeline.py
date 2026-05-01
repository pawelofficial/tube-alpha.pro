"""Video processing pipeline orchestrator.

Coordinates the full flow: URL → metadata → validation → transcript → sentiment.
This is the main service that higher-level code (API routes, CLI, scripts) should call.

Usage:
    from tube_alpha.services.pipeline import VideoPipeline
    from tube_alpha.config import Settings

    pipeline = VideoPipeline(Settings())

    # Process a single video
    result = pipeline.process_video("https://youtube.com/watch?v=abc123")

    # Get sentiment tiles for homepage
    tiles = pipeline.get_all_sentiment_tiles()

    # Scrape a channel and process all videos
    pipeline.scrape_and_process_channel("TheDavidLinReport")
"""

import logging
from typing import Dict, List, Optional

from tube_alpha.config import Settings
from tube_alpha.models import VideoProcessResponse
from tube_alpha.services.sentiment import SentimentService
from tube_alpha.services.youtube import YouTubeService, extract_video_id, _clean_title

logger = logging.getLogger(__name__)


class VideoPipeline:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._youtube = YouTubeService(settings)
        self._sentiment = SentimentService(settings)

    def process_video(self, url: str, video_id: Optional[str] = None) -> VideoProcessResponse:
        """Process a YouTube video end-to-end.

        Steps:
          1. Extract video ID from URL
          2. Check if already processed (return cached if so)
          3. Fetch title + description
          4. Validate with AI (is it about investing?)
          5. Fetch transcript
          6. Extract sentiments with AI
          7. Return results

        Returns a VideoProcessResponse.
        """
        # Step 1: Get video ID
        if not video_id:
            video_id = extract_video_id(url)
        if not video_id:
            return VideoProcessResponse(
                success=False, video_id="", standard_url=url,
                summary="Could not extract video ID from URL",
                message="Invalid YouTube URL",
            )

        standard_url = f"https://www.youtube.com/watch?v={video_id}"

        # Step 2: Check cache
        if self._youtube.is_video_processed(video_id):
            logger.info("Video %s already processed, returning cached data", video_id)
            summary = self._youtube.get_video_summary(video_id) or ""
            sentiments = self._sentiment.get_sentiments(video_id=video_id)
            tiles = self._sentiment.aggregate_tiles(sentiments)
            return VideoProcessResponse(
                success=True, video_id=video_id, standard_url=standard_url,
                summary=summary, tiles=tiles,
                message="Retrieved from cache",
            )

        # Step 3: Fetch metadata
        logger.info("Step 1/6: Fetching metadata for %s", video_id)
        title, description = self._youtube.get_video_metadata_with_rotation(video_id)
        if title == "error":
            return VideoProcessResponse(
                success=False, video_id=video_id, standard_url=standard_url,
                summary="Failed to fetch video metadata",
                message="Could not retrieve video title/description",
            )
        title = _clean_title(title)

        video_date = self._youtube.get_video_upload_date_with_rotation(video_id)

        # Step 4: Validate with AI
        logger.info("Step 2/6: Validating content for %s", video_id)
        validation = self._sentiment.validate_video(title, description)
        is_valid = validation["valid"]
        guest = validation.get("guest")
        summary = _clean_title(validation.get("summary") or "")

        # Step 5: Save metadata
        logger.info("Step 3/6: Saving metadata for %s", video_id)
        self._youtube.save_channel_metadata(
            video_id=video_id,
            title=title,
            guest=guest,
            description_summary=summary,
            valid=is_valid,
            video_date=video_date,
        )

        if not is_valid:
            logger.info("Video %s is not about investing, skipping", video_id)
            return VideoProcessResponse(
                success=False, video_id=video_id, standard_url=standard_url,
                summary="This video is not about investing",
                message="Video content is not investment-related",
            )

        # Step 6: Fetch and save transcript
        logger.info("Step 4/6: Fetching transcript for %s", video_id)
        transcript = self._youtube.fetch_transcript_with_rotation(video_id)

        logger.info("Step 5/6: Saving transcript for %s", video_id)
        self._youtube.save_transcript_to_db(video_id, transcript)

        # Step 7: Extract sentiments
        logger.info("Step 6/6: Extracting sentiments for %s", video_id)
        transcript_text = self._youtube.get_transcript_text(video_id)
        if transcript_text:
            parsed = self._sentiment.process_video_transcript(video_id, transcript_text)
        else:
            logger.warning("No transcript text found for %s after saving", video_id)
            parsed = []

        # Mark complete
        self._youtube.mark_transcript_parsed(video_id)

        # Build response
        sentiments = self._sentiment.get_sentiments(video_id=video_id)
        tiles = self._sentiment.aggregate_tiles(sentiments)

        return VideoProcessResponse(
            success=True, video_id=video_id, standard_url=standard_url,
            summary=summary, tiles=tiles,
            message=f"Processed {len(parsed)} sentiment items",
        )

    def get_all_sentiment_tiles(self, limit: int = 50) -> List[Dict]:
        """Get aggregated sentiment tiles for all videos."""
        sentiments = self._sentiment.get_sentiments(limit=limit)
        return self._sentiment.aggregate_tiles(sentiments)

    def get_video_sentiment_tiles(self, video_id: str) -> List[Dict]:
        """Get sentiment tiles for a specific video."""
        sentiments = self._sentiment.get_sentiments(video_id=video_id)
        return self._sentiment.aggregate_tiles(sentiments)

    def scrape_and_process_channel(
        self, channel_name: str, max_videos: Optional[int] = None
    ) -> Dict[str, int]:
        """Scrape a channel's videos and process transcripts through sentiment analysis.

        Args:
            channel_name: YouTube channel handle.
            max_videos: Number of recent videos to scrape. Defaults to config value.
        """
        stats = self._youtube.scrape_channel(channel_name, max_videos=max_videos)

        # Process any unprocessed transcripts
        unprocessed = self._youtube._db.fetch_scalars(
            "SELECT video_id FROM channels WHERE transcript_downloaded = 1 AND transcript_parsed = 0"
        )
        for vid in unprocessed:
            try:
                text = self._youtube.get_transcript_text(vid)
                if text:
                    self._sentiment.process_video_transcript(vid, text)
                    self._youtube.mark_transcript_parsed(vid)
                    logger.info("Processed transcript for %s", vid)
            except Exception as e:
                logger.error("Failed to process %s: %s", vid, e)

        return stats
