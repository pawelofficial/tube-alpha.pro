"""Tests for YouTubeService.scrape_channel.

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


def _make_service(settings: Settings) -> YouTubeService:
    """Build a YouTubeService without hitting the real DB."""
    svc = YouTubeService.__new__(YouTubeService)
    svc._settings = settings

    from tube_alpha.services.proxy import ProxyService
    svc._proxy = ProxyService(settings)

    # Stub the DB so execute/fetch calls are no-ops
    db = MagicMock()
    db.fetch_scalar.return_value = None   # is_video_processed → False
    db.execute.return_value = MagicMock(rowcount=1)
    svc._db = db

    return svc


def _fake_ydl_result(video_ids: list) -> dict:
    return {
        "entries": [
            {"id": vid, "title": f"Video {vid}", "upload_date": "20250101"}
            for vid in video_ids
        ]
    }


class TestScrapeChannelOptions(unittest.TestCase):
    """Verify yt-dlp options are built correctly."""

    def test_playlistend_set_to_count(self):
        settings = _make_settings(yt_vids_count=5)
        svc = _make_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.scrape_channel("TestChannel", max_videos=5)

        self.assertEqual(captured_opts.get("playlistend"), 5)

    def test_no_proxy_when_not_configured(self):
        settings = _make_settings(proxy_username="", proxy_password="")
        svc = _make_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.scrape_channel("TestChannel")

        self.assertNotIn("proxy", captured_opts)

    def test_proxy_included_when_configured(self):
        settings = _make_settings(proxy_username="user-2", proxy_password="secret")
        svc = _make_service(settings)

        captured_opts = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(extract_info=lambda url, **kw: _fake_ydl_result([]))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            svc.scrape_channel("TestChannel")

        self.assertIn("proxy", captured_opts)
        self.assertIn("secret", captured_opts["proxy"])

    def test_channel_url_uses_at_handle(self):
        settings = _make_settings()
        svc = _make_service(settings)

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
            svc.scrape_channel("kitco")

        self.assertTrue(any("@kitco" in u for u in captured_urls),
                        f"Expected @kitco in URL, got: {captured_urls}")


class TestScrapeChannelResults(unittest.TestCase):
    """Verify result counting and early-exit behaviour."""

    def _run_with_entries(self, video_ids, max_videos=3):
        settings = _make_settings(yt_vids_count=max_videos)
        svc = _make_service(settings)

        # fetch_transcript → return minimal transcript so pipeline doesn't error
        svc.fetch_transcript_with_rotation = MagicMock(
            return_value=[{"text": "hello", "start": 0.0, "duration": 1.0}]
        )
        svc.save_transcript_to_db = MagicMock()
        svc.get_video_upload_date_with_rotation = MagicMock(return_value=None)

        def fake_ydl(opts):
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(
                extract_info=lambda url, **kw: _fake_ydl_result(video_ids)
            )
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            with patch("tube_alpha.services.youtube.time") as mock_time:
                mock_time.sleep = MagicMock()
                return svc.scrape_channel("TestChannel", max_videos=max_videos)

    def test_zero_entries_returns_zero_counts(self):
        stats = self._run_with_entries([])
        self.assertEqual(stats, {"success": 0, "failed": 0})

    def test_three_entries_all_succeed(self):
        stats = self._run_with_entries(["aaa", "bbb", "ccc"], max_videos=3)
        self.assertEqual(stats["success"], 3)
        self.assertEqual(stats["failed"], 0)

    def test_max_videos_limits_processing(self):
        # 5 videos available but max_videos=2 — only 2 should be processed
        stats = self._run_with_entries(["a", "b", "c", "d", "e"], max_videos=2)
        self.assertEqual(stats["success"], 2)

    def test_transcript_failure_counts_as_failed(self):
        settings = _make_settings(yt_vids_count=2)
        svc = _make_service(settings)

        svc.fetch_transcript_with_rotation = MagicMock(
            side_effect=Exception("no transcript")
        )
        svc.save_transcript_to_db = MagicMock()
        svc.get_video_upload_date_with_rotation = MagicMock(return_value=None)

        def fake_ydl(opts):
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(
                extract_info=lambda url, **kw: _fake_ydl_result(["x1", "x2"])
            )
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tube_alpha.services.youtube.YoutubeDL", side_effect=fake_ydl):
            with patch("tube_alpha.services.youtube.time") as mock_time:
                mock_time.sleep = MagicMock()
                stats = svc.scrape_channel("TestChannel", max_videos=2)

        self.assertEqual(stats["failed"], 2)
        self.assertEqual(stats["success"], 0)


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
