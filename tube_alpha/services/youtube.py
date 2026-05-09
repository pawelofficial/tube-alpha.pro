"""YouTube scraping service.

Handles transcript downloading, metadata extraction, and channel scraping.
All database writes use parameterized queries.

Usage:
    from tube_alpha.services.youtube import YouTubeService
    from tube_alpha.config import Settings

    yt = YouTubeService(Settings())
    title, desc = yt.get_video_metadata("dQw4w9WgXcQ")
    transcript = yt.fetch_transcript("dQw4w9WgXcQ")
    yt.save_transcript_to_db("dQw4w9WgXcQ", transcript)
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
)
from youtube_transcript_api.proxies import WebshareProxyConfig
from yt_dlp import YoutubeDL

from tube_alpha.config import Settings
from tube_alpha.database import Database
from tube_alpha.services.proxy import ProxyService

logger = logging.getLogger(__name__)

# Browser-like headers for scraping
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from a YouTube URL. Returns None for shorts."""
    if "/shorts/" in url:
        return None

    # Strip query params after the video ID
    url = url.split("&")[0]

    match = re.search(
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([\w-]{11})",
        url,
    )
    return match.group(1) if match else None


def _clean_text(s: str) -> str:
    """Remove characters that could cause issues in database storage."""
    for ch in ("'", '"', "\\", "/"):
        s = s.replace(ch, "")
    return s


def _clean_title(title: str) -> str:
    """Remove problematic characters from title."""
    replacements = {
        "<": "", ">": "", ":": "-", '"': "'", "/": "-",
        "\\": "-", "|": "-", "?": "", "*": "", "'": "",
    }
    for char, repl in replacements.items():
        title = title.replace(char, repl)
    title = "".join(c for c in title if ord(c) >= 32)
    title = " ".join(title.split()).strip()
    return title


def _upload_iso_from_ytdlp_info(info: Optional[Dict[str, Any]]) -> Optional[str]:
    """Normalize yt-dlp `timestamp`, `release_timestamp`, or `upload_date` to UTC SQLite datetime."""
    if not info:
        return None
    for key in ("timestamp", "release_timestamp"):
        ts = info.get(key)
        if ts is not None:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, OSError, OverflowError, TypeError):
                pass
    ud = info.get("upload_date") or info.get("release_date")
    if ud:
        s = str(ud).strip()
        if len(s) == 8 and s.isdigit():
            try:
                dt = datetime(
                    int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=timezone.utc
                )
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
    return None


class YouTubeService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._db = Database(settings.data_db_path)
        self._proxy = ProxyService(settings)

    # --- YouTube client ---

    def _make_client(self, proxy_user_index: Optional[int] = None) -> YouTubeTranscriptApi:
        """Create a YouTubeTranscriptApi client. Uses proxy if configured, else direct."""
        if not self._proxy.is_configured:
            logger.info("Proxy not configured — fetching transcripts without proxy")
            return YouTubeTranscriptApi()

        if proxy_user_index is not None:
            username = f"{self._settings.proxy_base_username}-{proxy_user_index}"
        else:
            username = self._settings.proxy_username

        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=username,
                proxy_password=self._settings.proxy_password,
            )
        )

    # --- Transcript fetching ---

    def fetch_transcript(
        self, video_id: str, client: Optional[YouTubeTranscriptApi] = None
    ) -> List[Dict]:
        """Fetch transcript for a video. Returns list of {text, start, duration}."""
        if client is None:
            client = self._make_client()

        logger.info("Fetching transcript for %s", video_id)
        time.sleep(1)  # Rate limiting

        snippets = client.fetch(video_id, languages=[self._settings.yt_language])
        logger.info("Fetched %d transcript segments for %s", len(snippets), video_id)

        return [
            {"text": s.text, "start": s.start, "duration": s.duration}
            for s in snippets
        ]

    def fetch_transcript_with_rotation(self, video_id: str) -> List[Dict]:
        """Fetch transcript, rotating through proxy users on failure.

        Falls back to a direct (no-proxy) request if all proxy users fail,
        which handles cases where the proxy tunnel is broken but the video
        transcript is publicly accessible.
        """
        if not self._proxy.is_configured:
            return self.fetch_transcript(video_id)

        last_error = None
        for user_idx in self._proxy.iter_proxy_users():
            logger.info("Trying proxy user index %d for %s", user_idx, video_id)
            client = self._make_client(proxy_user_index=user_idx)
            try:
                return self.fetch_transcript(video_id, client=client)
            except (RequestBlocked, requests.exceptions.ProxyError) as e:
                logger.warning("Proxy user %d failed for %s: %s", user_idx, video_id, e)
                last_error = e
            except (TranscriptsDisabled, NoTranscriptFound) as e:
                # No point retrying with a different proxy
                raise
            except Exception as e:
                logger.warning("Proxy user %d error for %s: %s", user_idx, video_id, e)
                last_error = e

        # All proxy attempts failed — try direct as a last resort
        logger.warning(
            "All proxy attempts failed for %s (%s) — falling back to direct fetch",
            video_id, last_error,
        )
        return self.fetch_transcript(video_id, client=YouTubeTranscriptApi())

    # --- Metadata fetching ---

    def _extract_metadata_from_html(self, html: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract title and description from YouTube page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        title = None
        description = None

        # Method 1: JSON data embedded in page
        for script in soup.find_all("script"):
            if script.string and "var ytInitialPlayerResponse = " in script.string:
                try:
                    json_text = script.string.split("var ytInitialPlayerResponse = ")[1]
                    json_text = json_text.split("};")[0] + "}"
                    data = json.loads(json_text)
                    title = data.get("videoDetails", {}).get("title", "")
                    description = data.get("videoDetails", {}).get("shortDescription", "")
                    if title and description:
                        break
                except json.JSONDecodeError:
                    continue

        # Method 2: meta tags fallback
        if not title:
            tag = soup.find("meta", property="og:title")
            if tag:
                title = tag.get("content", "")

        if not description:
            tag = soup.find("meta", property="og:description")
            if tag:
                description = tag.get("content", "")

        return title, description

    def get_video_metadata(
        self, video_id: str, proxy_user_index: Optional[int] = None
    ) -> Tuple[str, str]:
        """Get video title and description. Returns (title, description)."""
        logger.info("Fetching metadata for %s", video_id)
        url = f"https://www.youtube.com/watch?v={video_id}"

        proxies = self._proxy.get_requests_proxy(proxy_user_index)
        try:
            resp = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Request failed for %s: %s", video_id, e)
            return "error", "error"

        title, description = self._extract_metadata_from_html(resp.text)

        if description:
            description = _clean_text(description)
        if title:
            title = _clean_title(title)

        if title and description:
            logger.info("Got metadata for %s: title=%d chars, desc=%d chars",
                        video_id, len(title), len(description))
            return title, description

        logger.warning("Incomplete metadata for %s", video_id)
        return title or "error", description or "error"

    def get_video_metadata_with_rotation(self, video_id: str) -> Tuple[str, str]:
        """Get metadata, rotating through proxy users on failure."""
        for user_idx in self._proxy.iter_proxy_users():
            logger.info("Trying proxy user %d for metadata of %s", user_idx, video_id)
            title, desc = self.get_video_metadata(video_id, proxy_user_index=user_idx)
            if title != "error" and desc != "error":
                return title, desc

        logger.error("All proxy attempts failed for metadata of %s", video_id)
        return "error", "error"

    # --- Upload date (yt-dlp; fills channels.video_date) ---

    def _ytdlp_opts_meta_only(self, proxy_user_index: Optional[int]) -> dict:
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
        }
        if self._proxy.is_configured:
            url = self._proxy.get_proxy_url(proxy_user_index)
            if url:
                opts["proxy"] = url
        return opts

    def get_video_upload_date(
        self, video_id: str, proxy_user_index: Optional[int] = None
    ) -> Optional[str]:
        """Return publish time as UTC ``YYYY-MM-DD HH:MM:SS`` via yt-dlp, or None."""
        watch = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with YoutubeDL(self._ytdlp_opts_meta_only(proxy_user_index)) as ydl:
                info = ydl.extract_info(watch, download=False)
            iso = _upload_iso_from_ytdlp_info(info)
            if iso:
                logger.info("Upload date for %s: %s", video_id, iso)
            return iso
        except Exception as e:
            logger.warning("yt-dlp could not get upload date for %s: %s", video_id, e)
            return None

    def get_video_upload_date_with_rotation(self, video_id: str) -> Optional[str]:
        if self._proxy.is_configured:
            for user_idx in self._proxy.iter_proxy_users():
                iso = self.get_video_upload_date(video_id, proxy_user_index=user_idx)
                if iso:
                    return iso
            return None
        return self.get_video_upload_date(video_id, proxy_user_index=None)

    # --- Database operations ---

    def save_channel_metadata(
        self,
        video_id: str,
        title: str,
        guest: Optional[str],
        description_summary: Optional[str],
        valid: bool,
        video_date: Optional[str] = None,
    ) -> None:
        """Insert or update channel/video metadata (UPSERT on ``video_id``)."""
        self._db.execute(
            """INSERT INTO channels (name, video_id, guest, description_summary, valid, video_date)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 name = excluded.name,
                 guest = excluded.guest,
                 description_summary = excluded.description_summary,
                 valid = excluded.valid,
                 video_date = COALESCE(excluded.video_date, channels.video_date)""",
            (title, video_id, guest, description_summary, str(valid), video_date),
        )
        logger.info("Saved channel metadata for %s", video_id)

    def save_transcript_to_db(self, video_id: str, transcript: List[Dict]) -> None:
        """Write transcript segments to database."""
        if not transcript:
            logger.warning("No transcript to save for %s", video_id)
            return

        logger.info("Saving %d transcript segments for %s", len(transcript), video_id)

        params = [
            (no, video_id, _clean_text(seg.get("text", "")), seg.get("start", 0), seg.get("duration", 0))
            for no, seg in enumerate(transcript)
        ]
        self._db.execute_many(
            "INSERT INTO transcripts (no, video_id, text, start, duration) VALUES (?, ?, ?, ?, ?)",
            params,
        )

        self._db.execute(
            "UPDATE channels SET transcript_downloaded = 1 WHERE video_id = ?",
            (video_id,),
        )
        logger.info("Transcript saved for %s", video_id)

    def get_transcript_text(self, video_id: str) -> Optional[str]:
        """Get concatenated transcript text for a video."""
        return self._db.fetch_scalar(
            """WITH cte AS (
                 SELECT text FROM transcripts WHERE video_id = ? ORDER BY no
               )
               SELECT group_concat(text, ' ') FROM cte""",
            (video_id,),
        )

    def is_video_processed(self, video_id: str) -> bool:
        """Check if a video has been fully processed (downloaded + parsed)."""
        result = self._db.fetch_scalar(
            """SELECT 1 FROM channels
               WHERE video_id = ? AND transcript_downloaded = 1 AND transcript_parsed = 1""",
            (video_id,),
        )
        return result is not None

    def get_video_summary(self, video_id: str) -> Optional[str]:
        """Get the description summary for a processed video."""
        return self._db.fetch_scalar(
            "SELECT description_summary FROM channels WHERE video_id = ?",
            (video_id,),
        )

    def mark_transcript_parsed(self, video_id: str) -> None:
        """Mark a video's transcript as parsed."""
        self._db.execute(
            "UPDATE channels SET transcript_parsed = 1 WHERE video_id = ?",
            (video_id,),
        )

    # --- Channel scraping ---

    def scrape_channel(self, channel_name: str, max_videos: Optional[int] = None) -> Dict[str, int]:
        """Scrape recent videos from a YouTube channel.

        Args:
            channel_name: YouTube channel handle (e.g. 'TheDavidLinReport').
            max_videos: Number of recent videos to scrape. Defaults to
                        settings.yt_vids_count (from config.yaml VIDS_COUNT).

        Returns dict with 'success' and 'failed' counts.
        """
        count = max_videos or self._settings.yt_vids_count
        url = f"https://www.youtube.com/@{channel_name}/videos"
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "playlistend": count,
        }
        if self._proxy.is_configured:
            proxy_url = self._proxy.get_proxy_url(0)
            if proxy_url:
                ydl_opts["proxy"] = proxy_url
                logger.info("Using proxy for channel listing of %s", channel_name)

        logger.info("Scraping channel %s for last %d videos (url=%s)", channel_name, count, url)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error("yt-dlp failed to fetch channel listing for %s: %s", channel_name, e)
            return {"success": 0, "failed": 0}

        if not result or "entries" not in result:
            logger.error("yt-dlp returned no entries for %s — result=%s", channel_name, result)
            return {"success": 0, "failed": 0}

        entries = result["entries"] or []
        logger.info("yt-dlp found %d total entries for %s, processing first %d", len(entries), channel_name, count)

        stats = {"success": 0, "failed": 0}

        for entry in entries[:count]:
            video_id = entry.get("id")
            title = entry.get("title", "unknown")
            if not video_id:
                logger.warning("Entry missing video_id, skipping: %s", entry)
                continue

            logger.info("Processing video %s (%s)", video_id, title)

            if self.is_video_processed(video_id):
                logger.info("Skipping %s — already processed", video_id)
                continue

            upload_iso = _upload_iso_from_ytdlp_info(entry)
            if not upload_iso:
                upload_iso = self.get_video_upload_date_with_rotation(video_id)

            try:
                self._db.execute(
                    """INSERT INTO channels (name, video_id, transcript_downloaded,
                                             transcript_parsed, video_date)
                       VALUES (?, ?, 0, 0, ?)
                       ON CONFLICT(video_id) DO UPDATE SET
                         video_date = COALESCE(channels.video_date, excluded.video_date)""",
                    (channel_name, video_id, upload_iso),
                )
            except Exception as e:
                logger.error("Failed to insert channel record for %s: %s", video_id, e)

            try:
                logger.info("Fetching transcript for %s", video_id)
                transcript = self.fetch_transcript_with_rotation(video_id)
                logger.info("Transcript fetched for %s (%d segments), saving", video_id, len(transcript))
                self.save_transcript_to_db(video_id, transcript)
                stats["success"] += 1
                logger.info("Successfully scraped %s", video_id)
            except Exception as e:
                logger.error("Failed to scrape transcript for %s: %s", video_id, e)
                stats["failed"] += 1

            time.sleep(3)

        logger.info("Channel scrape complete for %s: %s", channel_name, stats)
        return stats
