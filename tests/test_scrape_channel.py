"""Tests for the channel scraping flow.

Run all tests (fast, no network):
    python -m pytest tests/test_scrape_channel.py -v

Run integration tests too (hits real YouTube, ~10s per channel):
    python -m pytest tests/test_scrape_channel.py -v -m integration

Or run directly without pytest:
    python tests/test_scrape_channel.py
    python tests/test_scrape_channel.py --integration
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make sure the project root is on the path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tube_alpha.config import Settings
from tube_alpha.models import VideoProcessResponse
from tube_alpha.services.pipeline import VideoPipeline
from tube_alpha.services.youtube import YouTubeService


def _make_settings(**overrides) -> Settings:
    """Return a Settings instance with test-friendly defaults."""
    defaults = dict(
        data_db_path=Path(":memory:"),
        admin_db_path=Path(":memory:"),
        yt_channels=["TheDavidLinReport"],
        yt_vids_count=3,
        proxy_username="",
        proxy_password="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_youtube_service(settings: Settings) -> YouTubeService:
    """Build a YouTubeService without hitting the real DB."""
    svc = YouTubeService.__new__(YouTubeService)
    svc._settings = settings

    from tube_alpha.services.proxy import ProxyService
    svc._proxy = ProxyService(settings)

    db = MagicMock()
    db.fetch_scalar.return_value = None   # is_video_processed → False
    db.execute.return_value = MagicMock(rowcount=1)
    svc._db = db

    return svc


def _make_pipeline(settings: Settings) -> VideoPipeline:
    """Build a VideoPipeline with stubbed YouTube + sentiment services."""
    pipeline = VideoPipeline.__new__(VideoPipeline)
    pipeline._settings = settings
    pipeline._youtube = _make_youtube_service(settings)
    pipeline._sentiment = MagicMock()
    return pipeline


def _fake_ydl_result(video_ids: list) -> dict:
    return {
        "entries": [
            {"id": vid, "title": f"Video {vid}", "upload_date": "20250101"}
            for vid in video_ids
        ]
    }


class TestListChannelVideosOptions(unittest.TestCase):
    """Verify yt-dlp options are built correctly when listing channel videos."""

    def test_playlistend_set_to_count(self):
        settings = _make_settings(yt_vids_count=5)
        svc = _make_youtube_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.list_channel_videos("TestChannel", max_videos=5)

        self.assertEqual(captured_opts.get("playlistend"), 5)

    def test_no_proxy_when_not_configured(self):
        settings = _make_settings(proxy_username="", proxy_password="")
        svc = _make_youtube_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.list_channel_videos("TestChannel")

        self.assertNotIn("proxy", captured_opts)

    def test_proxy_included_when_configured(self):
        settings = _make_settings(proxy_username="user-2", proxy_password="secret")
        svc = _make_youtube_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.list_channel_videos("TestChannel")

        self.assertIn("proxy", captured_opts)
        self.assertIn("secret", captured_opts["proxy"])

    def test_channel_url_uses_at_handle(self):
        settings = _make_settings()
        svc = _make_youtube_service(settings)

        captured_urls = []

        def fake_ydl(opts):
            cm = MagicMock()

            def fake_extract(url, **kw):
                captured_urls.append(url)
                return _fake_ydl_result([])

            cm.__enter__ = lambda s: MagicMock(extract_info=fake_extract)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.list_channel_videos("kitco")

        self.assertTrue(any("@kitco" in u for u in captured_urls),
                        f"Expected @kitco in URL, got: {captured_urls}")

    def test_returns_video_ids_and_metadata(self):
        settings = _make_settings()
        svc = _make_youtube_service(settings)

        def fake_ydl(opts):
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(
                extract_info=lambda url, **kw: _fake_ydl_result(["a1", "b2"])
            )
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            videos = svc.list_channel_videos("TestChannel", max_videos=5)

        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0]["id"], "a1")
        self.assertEqual(videos[0]["title"], "Video a1")
        self.assertEqual(videos[0]["upload_date"], "2025-01-01 00:00:00")


class TestScrapeAndProcessChannel(unittest.TestCase):
    """Verify the scheduler flow shares the user-submission pipeline."""

    def _run(self, video_ids, max_videos=3, process_results=None, processed_ids=None):
        settings = _make_settings(yt_vids_count=max_videos)
        pipeline = _make_pipeline(settings)

        processed_ids = set(processed_ids or [])
        pipeline._youtube.is_video_processed = MagicMock(
            side_effect=lambda vid: vid in processed_ids
        )
        pipeline._youtube._db.fetch_scalars = MagicMock(return_value=[])

        # Default: every video succeeds
        if process_results is None:
            process_results = {
                vid: VideoProcessResponse(
                    success=True, video_id=vid, standard_url=f"u/{vid}",
                    summary="ok", message="processed",
                )
                for vid in video_ids
            }
        pipeline.process_video = MagicMock(side_effect=lambda url, video_id: process_results[video_id])

        with patch("tube_alpha.services.pipeline.time") as mock_time:
            mock_time.sleep = MagicMock()
            with patch.object(
                pipeline._youtube, "list_channel_videos",
                return_value=[{"id": v, "title": f"T {v}", "upload_date": None} for v in video_ids],
            ):
                stats = pipeline.scrape_and_process_channel("TestChannel", max_videos=max_videos)

        return stats, pipeline

    def test_zero_entries_returns_zero_counts(self):
        stats, _ = self._run([])
        self.assertEqual(stats, {"success": 0, "skipped": 0, "failed": 0})

    def test_three_entries_all_succeed(self):
        stats, _ = self._run(["aaa", "bbb", "ccc"])
        self.assertEqual(stats["success"], 3)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["skipped"], 0)

    def test_already_processed_videos_are_skipped(self):
        stats, pipeline = self._run(
            ["a", "b", "c"],
            processed_ids={"b"},
        )
        self.assertEqual(stats["success"], 2)
        self.assertEqual(stats["skipped"], 1)
        # process_video must NOT be called for the cached video
        called_ids = [c.kwargs.get("video_id") for c in pipeline.process_video.call_args_list]
        self.assertNotIn("b", called_ids)

    def test_invalid_video_counts_as_skipped(self):
        # process_video returns success=False for non-investing content
        results = {
            "v1": VideoProcessResponse(
                success=True, video_id="v1", standard_url="u/v1",
                summary="ok", message="processed",
            ),
            "v2": VideoProcessResponse(
                success=False, video_id="v2", standard_url="u/v2",
                summary="not investing", message="rejected",
            ),
        }
        stats, _ = self._run(["v1", "v2"], process_results=results)
        self.assertEqual(stats["success"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["failed"], 0)

    def test_exception_counts_as_failed(self):
        settings = _make_settings(yt_vids_count=2)
        pipeline = _make_pipeline(settings)

        pipeline._youtube.is_video_processed = MagicMock(return_value=False)
        pipeline._youtube._db.fetch_scalars = MagicMock(return_value=[])
        pipeline.process_video = MagicMock(side_effect=Exception("boom"))

        with patch("tube_alpha.services.pipeline.time") as mock_time:
            mock_time.sleep = MagicMock()
            with patch.object(
                pipeline._youtube, "list_channel_videos",
                return_value=[{"id": "x1", "title": "T", "upload_date": None},
                              {"id": "x2", "title": "T", "upload_date": None}],
            ):
                stats = pipeline.scrape_and_process_channel("TestChannel", max_videos=2)

        self.assertEqual(stats["failed"], 2)
        self.assertEqual(stats["success"], 0)

    def test_video_processed_via_process_video_path(self):
        """Regression: scheduler must call process_video so channels.name=title,
        guest, description_summary, valid all get populated by AI validation."""
        stats, pipeline = self._run(["abc"])
        pipeline.process_video.assert_called_once()
        call = pipeline.process_video.call_args
        self.assertEqual(call.kwargs.get("video_id"), "abc")


# ---------------------------------------------------------------------------
# Integration tests — skipped unless --integration flag is passed
# These actually hit YouTube. Useful to verify @handles are correct.
# ---------------------------------------------------------------------------

INTEGRATION = "--integration" in sys.argv or any(
    getattr(m, "keywords", {}).get("integration") for m in []
)

try:
    import pytest
    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False


class TestChannelHandlesIntegration(unittest.TestCase):
    """Hit real YouTube to verify configured channel handles resolve correctly.

    Run with:  python tests/test_scrape_channel.py --integration
    Or pytest: python -m pytest tests/test_scrape_channel.py -v -m integration
    """

    def setUp(self):
        if not INTEGRATION:
            self.skipTest("Pass --integration to run live YouTube checks")

    def _check_handle(self, handle: str):
        from yt_dlp import YoutubeDL
        url = f"https://www.youtube.com/@{handle}/videos"
        opts = {"extract_flat": True, "quiet": True, "playlistend": 3}
        print(f"\nChecking @{handle} → {url}")
        with YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
        entries = result.get("entries", []) if result else []
        print(f"  Found {len(entries)} entries")
        for e in entries[:3]:
            print(f"  - {e.get('id')} | {e.get('title', '')[:60]}")
        return entries

    def test_TheDavidLinReport(self):
        entries = self._check_handle("TheDavidLinReport")
        self.assertGreater(len(entries), 0, "@TheDavidLinReport returned 0 entries — handle may be wrong")

    def test_kitco(self):
        entries = self._check_handle("kitco")
        self.assertGreater(len(entries), 0, "@kitco returned 0 entries — try @KitcoNews")

    def test_InvestingNews(self):
        entries = self._check_handle("InvestingNews")
        self.assertGreater(len(entries), 0, "@InvestingNews returned 0 entries — handle may be wrong")


if __name__ == "__main__":
    # Strip --integration from argv so unittest doesn't choke on it
    sys.argv = [a for a in sys.argv if a != "--integration"]
    unittest.main(verbosity=2)
