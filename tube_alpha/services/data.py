"""Data service layer for frontend consumption.

Each public method answers a specific question the frontend might ask.
The pattern is:  question → SQL → reshape → return structured data.

Usage:
    from tube_alpha.services.data import DataService
    from tube_alpha.config import Settings

    data = DataService(Settings())

    # What does this video talk about?
    result = data.video_overview("E5F4Vffnc_E")

    # What's the sentiment on gold?
    result = data.asset_overview("gold")

    # What's the sentiment on gold? (filtered)
    result = data.asset_overview("gold", from_date="2024-01-01", sentiment="bullish")

    # What has this guest said?
    result = data.guest_overview("Nassim Taleb")

    # What's the latest across everything?
    result = data.latest_overview(limit=50, from_date="2024-06-01")
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from tube_alpha.config import Settings
from tube_alpha.database import Database

logger = logging.getLogger(__name__)


def _apply_filters(
    base_where: str,
    base_params: tuple,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    sentiment: Optional[str] = None,
) -> Tuple[str, tuple]:
    """Append optional date/sentiment filters to a WHERE clause.

    Returns (where_clause, params) with filters applied.
    """
    clauses = [base_where] if base_where else []
    params = list(base_params)

    if from_date:
        clauses.append("a.ts >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("a.ts <= ?")
        params.append(to_date)
    if sentiment:
        clauses.append("LOWER(a.sentiment) LIKE ?")
        params.append(f"%{sentiment.lower()}%")

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, tuple(params)


def _sentiment_score(text: str) -> float:
    s = text.lower()
    score = 0.0
    if "bullish" in s:
        score += 1
    if "bearish" in s:
        score -= 1
    if "caution" in s:
        score -= 0.5
    if "conditional" in s:
        score *= 0.7
    return score


def _build_tiles(rows: List[Dict], separator: str) -> List[Dict]:
    """Aggregate raw answer rows into visualization tiles grouped by asset."""
    agg = defaultdict(lambda: {
        "scores": [], "sentiments": [], "quotes": set(),
        "videos": set(), "guests": set(), "last_ts": None,
    })

    for r in rows:
        a = agg[r["asset"]]
        a["scores"].append(_sentiment_score(r["sentiment"]))
        a["sentiments"].append(r["sentiment"])
        for q in (r.get("quotes") or "").split(separator):
            q = q.strip()
            if q:
                a["quotes"].add(q)
        a["videos"].add(r["video_id"])
        if r.get("guest"):
            a["guests"].add(r["guest"])
        a["last_ts"] = max(a["last_ts"], r["ts"]) if a["last_ts"] else r["ts"]

    tiles = []
    for asset, a in agg.items():
        avg = round(sum(a["scores"]) / len(a["scores"]), 2) if a["scores"] else 0
        tiles.append({
            "asset": asset,
            "avg_score": avg,
            "sentiments": sorted(set(a["sentiments"])),
            "quotes": sorted(a["quotes"]),
            "videos": sorted(a["videos"]),
            "guests": sorted(a["guests"]),
            "last_ts": a["last_ts"],
        })

    tiles.sort(key=lambda x: x["avg_score"], reverse=True)
    return tiles


class DataService:
    def __init__(self, settings: Settings):
        self._db = Database(settings.data_db_path)
        self._sep = settings.quote_separator

    # ------------------------------------------------------------------
    # Use case: What does this video talk about?
    # ------------------------------------------------------------------
    def video_overview(self, video_id: str) -> Dict[str, Any]:
        """Full picture for a single video: metadata + sentiment tiles."""
        meta = self._db.fetch_one(
            """SELECT video_id, name AS title, guest, description_summary,
                      valid, video_date, ts
               FROM channels WHERE video_id = ?""",
            (video_id,),
        )
        if not meta:
            return {"video_id": video_id, "found": False}

        rows = self._db.fetch_all(
            """SELECT a.asset, a.sentiment, a.quotes, a.video_id, a.ts,
                      c.guest
               FROM answers a
               JOIN channels c ON c.video_id = a.video_id
               WHERE a.video_id = ?
               ORDER BY a.ts DESC""",
            (video_id,),
        )

        return {
            "video_id": video_id,
            "found": True,
            "title": meta.get("title"),
            "guest": meta.get("guest"),
            "summary": meta.get("description_summary"),
            "video_date": meta.get("video_date"),
            "tiles": _build_tiles(rows, self._sep),
        }

    # ------------------------------------------------------------------
    # Use case: What's the sentiment on <asset>?
    # ------------------------------------------------------------------
    def asset_overview(
        self,
        asset: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """All sentiment data for a given asset across all videos."""
        where, params = _apply_filters(
            "LOWER(a.asset) LIKE ?", (f"%{asset.lower()}%",),
            from_date=from_date, to_date=to_date, sentiment=sentiment,
        )
        rows = self._db.fetch_all(
            f"""SELECT a.asset, a.sentiment, a.quotes, a.video_id, a.ts,
                      c.guest, c.name AS title
               FROM answers a
               JOIN channels c ON c.video_id = a.video_id
               WHERE {where}
               ORDER BY a.ts DESC""",
            params,
        )

        if not rows:
            return {"asset": asset, "found": False, "mentions": []}

        # Build per-video breakdown
        mentions = []
        for r in rows:
            mentions.append({
                "video_id": r["video_id"],
                "title": r.get("title"),
                "guest": r.get("guest"),
                "sentiment": r["sentiment"],
                "score": _sentiment_score(r["sentiment"]),
                "quotes": [q.strip() for q in (r.get("quotes") or "").split(self._sep) if q.strip()],
                "ts": r["ts"],
            })

        scores = [m["score"] for m in mentions]
        avg = round(sum(scores) / len(scores), 2) if scores else 0

        return {
            "asset": asset,
            "found": True,
            "avg_score": avg,
            "mention_count": len(mentions),
            "mentions": mentions,
        }

    # ------------------------------------------------------------------
    # Use case: What has this guest/interviewee said?
    # ------------------------------------------------------------------
    def guest_overview(
        self,
        guest: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """All sentiment data from videos featuring a specific guest."""
        where, params = _apply_filters(
            "LOWER(c.guest) LIKE ?", (f"%{guest.lower()}%",),
            from_date=from_date, to_date=to_date, sentiment=sentiment,
        )
        rows = self._db.fetch_all(
            f"""SELECT a.asset, a.sentiment, a.quotes, a.video_id, a.ts,
                      c.guest, c.name AS title, c.description_summary
               FROM answers a
               JOIN channels c ON c.video_id = a.video_id
               WHERE {where}
               ORDER BY a.ts DESC""",
            params,
        )

        if not rows:
            return {"guest": guest, "found": False, "videos": [], "tiles": []}

        # Collect unique videos
        videos_seen = {}
        for r in rows:
            vid = r["video_id"]
            if vid not in videos_seen:
                videos_seen[vid] = {
                    "video_id": vid,
                    "title": r.get("title"),
                    "summary": r.get("description_summary"),
                    "ts": r["ts"],
                }

        return {
            "guest": guest,
            "found": True,
            "video_count": len(videos_seen),
            "videos": list(videos_seen.values()),
            "tiles": _build_tiles(rows, self._sep),
        }

    # ------------------------------------------------------------------
    # Use case: What's the latest across everything?
    # ------------------------------------------------------------------
    def latest_overview(
        self,
        limit: int = 50,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Most recent sentiment data across all videos, aggregated as tiles."""
        where, params = _apply_filters(
            "", (),
            from_date=from_date, to_date=to_date, sentiment=sentiment,
        )
        rows = self._db.fetch_all(
            f"""SELECT a.asset, a.sentiment, a.quotes, a.video_id, a.ts,
                      c.guest
               FROM answers a
               JOIN channels c ON c.video_id = a.video_id
               {"WHERE " + where if where != "1=1" else ""}
               ORDER BY a.ts DESC
               LIMIT ?""",
            params + (limit,),
        )

        return {
            "total_rows": len(rows),
            "tiles": _build_tiles(rows, self._sep),
        }

    # ------------------------------------------------------------------
    # Use case: What videos have been processed?
    # ------------------------------------------------------------------
    def videos_list(self, limit: int = 50) -> List[Dict]:
        """List processed videos with basic metadata."""
        return self._db.fetch_all(
            """SELECT video_id, name AS title, guest, description_summary AS summary,
                      valid, video_date, ts
               FROM channels
               WHERE transcript_parsed = 1
               ORDER BY ts DESC
               LIMIT ?""",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Use case: What assets are being tracked?
    # ------------------------------------------------------------------
    def assets_list(self) -> List[Dict]:
        """List all unique assets with their aggregate score and mention count."""
        rows = self._db.fetch_all(
            """SELECT a.asset,
                      COUNT(*) AS mention_count,
                      COUNT(DISTINCT a.video_id) AS video_count,
                      MAX(a.ts) AS last_ts
               FROM answers a
               GROUP BY a.asset
               ORDER BY mention_count DESC"""
        )

        for r in rows:
            # Get avg score for each asset
            sentiments = self._db.fetch_all(
                "SELECT sentiment FROM answers WHERE asset = ?",
                (r["asset"],),
            )
            scores = [_sentiment_score(s["sentiment"]) for s in sentiments]
            r["avg_score"] = round(sum(scores) / len(scores), 2) if scores else 0

        return rows

    def distinct_asset_names(self) -> List[str]:
        """Distinct asset labels from ``vw_assets`` (alphabetical, non-empty)."""
        rows = self._db.fetch_all(
            """SELECT asset FROM vw_assets
               WHERE asset IS NOT NULL AND TRIM(asset) != ''
               ORDER BY asset COLLATE NOCASE"""
        )
        return [str(r["asset"]).strip() for r in rows if r.get("asset") is not None]

    # ------------------------------------------------------------------
    # Use case: What guests have been interviewed?
    # ------------------------------------------------------------------
    def guests_list(self) -> List[Dict]:
        """List all unique guests with video count."""
        return self._db.fetch_all(
            """SELECT guest,
                      COUNT(*) AS video_count,
                      MAX(ts) AS last_ts
               FROM channels
               WHERE guest IS NOT NULL AND guest != ''
               GROUP BY guest
               ORDER BY last_ts DESC"""
        )

    # ------------------------------------------------------------------
    # Use case: Dashboard overview — aggregate stats across all data
    # ------------------------------------------------------------------
    def dashboard_overview(
        self,
        asset: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        guest: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate stats, leaderboards, and timeline for the dashboard.

        All four queries share the same filter conditions so every panel
        reflects the same slice of data.  User values are passed as
        parameterised ``?`` arguments — the f-string only inserts our own
        hardcoded SQL fragments, never user input.
        """
        conds: List[str] = ["1=1"]
        cparams: list = []
        if asset:
            conds.append("a.asset = ?")
            cparams.append(asset)
        if from_date:
            conds.append("c.video_date >= ?")
            cparams.append(from_date)
        if to_date:
            conds.append("c.video_date <= ?")
            cparams.append(to_date)
        if guest:
            conds.append("c.guest = ?")
            cparams.append(guest)
        if sentiment:
            conds.append("LOWER(a.sentiment) LIKE ?")
            cparams.append(f"%{sentiment.lower()}%")

        where = " AND ".join(conds)
        wp = tuple(cparams)

        stats = self._db.fetch_one(f"""
            SELECT
                COUNT(DISTINCT c.video_id)  AS total_videos,
                COUNT(DISTINCT a.asset)     AS total_assets,
                COUNT(a.no)                 AS total_mentions,
                MIN(c.video_date)           AS earliest_date,
                MAX(c.video_date)           AS latest_date
            FROM channels c
            JOIN answers a ON a.video_id = c.video_id
            WHERE {where}
        """, wp) or {}

        timeline = self._db.fetch_all(f"""
            SELECT
                DATE(c.video_date)                                                          AS date,
                COUNT(*)                                                                    AS mention_count,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bullish%' THEN 1 ELSE 0 END)       AS bullish,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bearish%' THEN 1 ELSE 0 END)       AS bearish
            FROM channels c
            JOIN answers a ON a.video_id = c.video_id
            WHERE {where} AND c.video_date IS NOT NULL
            GROUP BY DATE(c.video_date)
            ORDER BY date ASC
            LIMIT 30
        """, wp)

        assets = self._db.fetch_all(f"""
            SELECT
                a.asset,
                COUNT(*)                                                                    AS mention_count,
                COUNT(DISTINCT a.video_id)                                                  AS video_count,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bullish%' THEN 1 ELSE 0 END)       AS bullish,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bearish%' THEN 1 ELSE 0 END)       AS bearish,
                MAX(a.ts)                                                                   AS last_ts
            FROM answers a
            JOIN channels c ON c.video_id = a.video_id
            WHERE {where}
            GROUP BY a.asset
            ORDER BY mention_count DESC
            LIMIT 20
        """, wp)
        for row in assets:
            b  = row.get("bullish") or 0
            be = row.get("bearish") or 0
            cnt = row["mention_count"] or 1
            row["net_score"] = round((b - be) / cnt, 2)

        guests = self._db.fetch_all(f"""
            SELECT
                c.guest,
                COUNT(DISTINCT c.video_id)                                                  AS video_count,
                COUNT(a.no)                                                                 AS mention_count,
                MAX(c.video_date)                                                           AS last_date,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bullish%' THEN 1 ELSE 0 END)       AS bullish,
                SUM(CASE WHEN LOWER(a.sentiment) LIKE '%bearish%' THEN 1 ELSE 0 END)       AS bearish
            FROM channels c
            JOIN answers a ON a.video_id = c.video_id
            WHERE {where} AND c.guest IS NOT NULL AND TRIM(c.guest) != ''
            GROUP BY c.guest
            ORDER BY video_count DESC, last_date DESC
        """, wp)
        for row in guests:
            b  = row.get("bullish") or 0
            be = row.get("bearish") or 0
            cnt = row["mention_count"] or 1
            row["net_score"] = round((b - be) / cnt, 2)

        return {
            "stats": stats,
            "timeline": timeline,
            "assets": assets,
            "guests": guests,
        }

    def filter_options(self) -> Dict[str, Any]:
        """Distinct values for dashboard filter dropdowns."""
        assets = self._db.fetch_scalars(
            """SELECT DISTINCT asset FROM answers
               WHERE asset IS NOT NULL AND TRIM(asset) != ''
               ORDER BY asset COLLATE NOCASE"""
        )
        guests = self._db.fetch_scalars(
            """SELECT DISTINCT guest FROM channels
               WHERE guest IS NOT NULL AND TRIM(guest) != ''
               ORDER BY guest COLLATE NOCASE"""
        )
        sentiments = self._db.fetch_scalars(
            """SELECT DISTINCT sentiment FROM answers
               WHERE sentiment IS NOT NULL AND TRIM(sentiment) != ''
               ORDER BY sentiment COLLATE NOCASE"""
        )
        date_range = self._db.fetch_one(
            """SELECT MIN(video_date) AS min_video_date,
                      MAX(video_date) AS max_video_date
               FROM channels WHERE video_date IS NOT NULL"""
        ) or {}
        return {
            "assets": assets,
            "guests": guests,
            "sentiments": sentiments,
            "date_range": date_range,
        }
