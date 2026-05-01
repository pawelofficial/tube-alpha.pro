"""Sentiment analysis service.

Uses OpenAI to extract investment sentiment from YouTube transcripts.

Usage:
    from tube_alpha.services.sentiment import SentimentService
    from tube_alpha.config import Settings

    service = SentimentService(Settings())

    # Validate if a video is about investing
    result = service.validate_video("Title", "Description text...")
    # result = {"valid": True, "guest": "John Doe", "summary": "..."}

    # Extract sentiments from transcript text
    sentiments = service.extract_sentiments("full transcript text...")
    # sentiments = [{"asset": "Gold", "sentiment": "bullish", "quotes": [...]}]
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import tiktoken
from openai import OpenAI

from tube_alpha.config import Settings
from tube_alpha.database import Database

logger = logging.getLogger(__name__)

_VALIDATE_PROMPT = """
You will receive a raw YouTube video description.
Your task is to:
1. Check if the input is valid (i.e., an actual interview description, not ads or unrelated text).
2. Summarize the main topic of the interview briefly.
3. Extract the guest's firstname and lastname without any titles like Dr. Prof. etc, if mentioned.
The most important part is to indicate whether the video is about investing or not, because user can input whatever link from youtube. 
Set the video as valid if either title make sense or description make sense.

Return the result strictly in this JSON format:
{
  "valid": true or false,
  "interview_summary": "<short summary of the interview, else null>",
  "guest": "<guest name if found, else null>"
}
"""

_SENTIMENT_PROMPT = """
Extract sentiment about asset classes from User provided Raw Transcript, output the result in a json list format with "asset", "sentiment" and "quotes" fields.
Make sure quotes are relevant to the sentiment.
Output example:
[
    {
        "asset": "asset1",
        "sentiment": "(example) bullish longterm",
        "quotes":["(example) I think gold is a great investment right now"]
    },
    {
        "asset": "asset2",
        "sentiment": "(example) bearish shortterm",
        "quotes":["(example) FED will raise rates","(example) Jpowell is hawkish"]
    }
]
"""


class SentimentService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._db = Database(settings.data_db_path)
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._max_tokens = settings.max_tokens_per_chunk
        self._encoding = None  # Lazy-loaded (requires network on first use)

    @property
    def encoding(self):
        if self._encoding is None:
            self._encoding = tiktoken.encoding_for_model("gpt-4")
        return self._encoding

    def validate_video(self, title: str, description: str) -> Dict[str, Any]:
        """Ask the LLM whether a video is about investing.

        Returns dict with keys: valid (bool), guest (str|None), summary (str|None).
        """
        user_content = f"title: {title}\nraw YouTube video description: {description}"
        logger.info(
            "Validating video: title=%s (desc %d chars)",
            title[:80],
            len(description or ""),
        )
        logger.debug("Validation input: %s", user_content[:500])

        messages = [
            {"role": "system", "content": _VALIDATE_PROMPT},
            {"role": "user", "content": user_content},
        ]
        logger.debug("Validation messages: %s", messages)
        resp = self._client.chat.completions.create(model=self._model, messages=messages)
        answer = resp.choices[0].message.content

        # Strip markdown code fences if present
        answer = answer.replace("```json", "```")
        answer = re.sub(r"^```[a-z]*\n|\n```$", "", answer.strip())

        logger.info("Validation LLM response: %s", answer)

        parsed = json.loads(answer)
        return {
            "valid": parsed.get("valid", False),
            "guest": parsed.get("guest"),
            "summary": parsed.get("interview_summary"),
        }

    def extract_sentiments(self, transcript_text: str) -> List[Dict[str, Any]]:
        """Extract asset sentiments from transcript text.

        Returns list of dicts with keys: asset, sentiment, quotes.
        """
        all_parsed = []
        max_chunks = self._settings.sentiment_max_chunks

        for chunk_no, chunk in enumerate(self._split_by_tokens(transcript_text), start=1):
            messages = [
                {"role": "system", "content": _SENTIMENT_PROMPT},
                {"role": "user", "content": json.dumps(chunk)},
            ]
            logger.info(
                "Processing chunk %d%s of %d characters",
                chunk_no,
                f"/{max_chunks}" if max_chunks > 0 else "",
                len(chunk),
            )
            
            logger.debug("Sentiment messages: %s", messages)
            resp = self._client.chat.completions.create(model=self._model, messages=messages)
            answer = resp.choices[0].message.content
            logger.debug("Sentiment answer: %s", answer)
            
            _, parsed = self._parse_completion(answer)
            all_parsed.extend(parsed)

            if max_chunks > 0 and chunk_no >= max_chunks:
                break

        return all_parsed

    def save_sentiments_to_db(
        self,
        video_id: str,
        parsed_sentiments: List[Dict],
        raw_text: str = "",
    ) -> None:
        """Write sentiment results to database using parameterized queries."""
        sep = self._settings.quote_separator

        # Insert parsed answers
        answer_params = [
            (no, video_id, item.get("asset", ""), item.get("sentiment", ""), sep.join(item.get("quotes", [])))
            for no, item in enumerate(parsed_sentiments)
        ]
        self._db.execute_many(
            "INSERT INTO answers (no, video_id, asset, sentiment, quotes) VALUES (?, ?, ?, ?, ?)",
            answer_params,
        )

        # Insert raw answer text
        if raw_text:
            self._db.execute(
                "INSERT INTO raw_answers (video_id, text) VALUES (?, ?)",
                (video_id, raw_text),
            )

        logger.info("Saved sentiments for %s: %d items", video_id, len(parsed_sentiments))

    def process_video_transcript(self, video_id: str, transcript_text: str) -> List[Dict]:
        """Full pipeline: extract sentiments from transcript and save to DB.

        Returns the parsed sentiment list.
        """
        parsed = self.extract_sentiments(transcript_text)
        raw_text = json.dumps(parsed)
        self.save_sentiments_to_db(video_id, parsed, raw_text=raw_text)
        return parsed

    # --- Sentiment data retrieval ---

    def get_sentiments(
        self, video_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict]:
        """Retrieve sentiment data, optionally filtered by video_id."""
        sep = self._settings.quote_separator

        if video_id:
            rows = self._db.fetch_all(
                "SELECT asset, sentiment, quotes, video_id, ts FROM answers WHERE video_id = ? ORDER BY ts DESC",
                (video_id,),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT asset, sentiment, quotes, video_id, ts FROM answers ORDER BY ts DESC LIMIT ?",
                (limit,),
            )

        for row in rows:
            row["quotes"] = row["quotes"].split(sep) if row.get("quotes") else []

        return rows

    @staticmethod
    def sentiment_score(sentiment_text: str) -> float:
        """Convert a sentiment string to a numeric score."""
        s = sentiment_text.lower()
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

    def aggregate_tiles(self, rows: List[Dict]) -> List[Dict]:
        """Aggregate sentiment rows into tiles for display."""
        from collections import defaultdict

        agg = defaultdict(lambda: {
            "scores": [], "sentiments": [], "quotes": set(),
            "videos": set(), "last_ts": None,
        })

        for r in rows:
            a = agg[r["asset"]]
            a["scores"].append(self.sentiment_score(r["sentiment"]))
            a["sentiments"].append(r["sentiment"])
            for q in r.get("quotes", []):
                q = q.strip()
                if q:
                    a["quotes"].add(q)
            a["videos"].add(r["video_id"])
            a["last_ts"] = max(a["last_ts"], r["ts"]) if a["last_ts"] else r["ts"]

        tiles = []
        for asset, a in agg.items():
            avg = round(sum(a["scores"]) / len(a["scores"]), 2) if a["scores"] else 0
            tiles.append({
                "asset": asset,
                "avg": avg,
                "sentiments": sorted(set(a["sentiments"])),
                "quotes": sorted(a["quotes"]),
                "videos": sorted(a["videos"]),
                "last_ts": a["last_ts"],
            })

        tiles.sort(key=lambda x: x["avg"], reverse=True)
        return tiles

    # --- Internal helpers ---

    def _split_by_tokens(self, text: str) -> List[str]:
        """Split text into chunks respecting token limits and sentence boundaries."""
        raw_chunks = list(self._split_raw(text))

        adjusted = []
        carry = ""
        for i, chunk in enumerate(raw_chunks):
            chunk = carry + chunk

            if i < len(raw_chunks) - 1:
                last_boundary = max(chunk.rfind("."), chunk.rfind("!"), chunk.rfind("?"))
                if last_boundary != -1:
                    adjusted.append(chunk[: last_boundary + 1])
                    carry = chunk[last_boundary + 1 :].strip() + " "
                else:
                    adjusted.append(chunk)
                    carry = ""
            else:
                adjusted.append(chunk)

        return adjusted

    def _split_raw(self, text: str):
        """Split text into chunks by token count."""
        words = text.split()
        current = ""

        for word in words:
            test = current + (" " if current else "") + word
            if len(self.encoding.encode(test)) > self._max_tokens and current:
                yield current
                current = word
            else:
                current = test

        if current:
            yield current

    def _parse_completion(self, completion: str) -> Tuple[str, List[Dict]]:
        """Parse JSON from OpenAI completion, with regex fallback."""
        # Try standard JSON parsing
        try:
            normalized = completion
            normalized = re.sub(r"\bTrue\b", "true", normalized)
            normalized = re.sub(r"\bFalse\b", "false", normalized)
            normalized = re.sub(r"\bNone\b", "null", normalized)

            # Convert single quotes to double, preserving contractions
            normalized = re.sub(r"(\w)'(\w)", r"\1ESCAPEDQUOTE\2", normalized)
            normalized = re.sub(r"(?<!\\)'", '"', normalized)
            normalized = normalized.replace("ESCAPEDQUOTE", "'")

            parsed = json.loads(normalized)
            return json.dumps(parsed, indent=4), parsed
        except (json.JSONDecodeError, TypeError):
            logger.warning("JSON parse failed, falling back to regex")

        # Regex fallback
        results = []
        for obj_match in re.finditer(r"\{[^{}]*?\}", completion, re.DOTALL):
            obj_str = obj_match.group(0)
            obj_dict = {}

            asset = re.search(r"""["\']asset["\']\s*:\s*["\']([^"\']*)["\']""", obj_str)
            if asset:
                obj_dict["asset"] = asset.group(1)

            sentiment = re.search(r"""["\']sentiment["\']\s*:\s*["\']([^"\']*)["\']""", obj_str)
            if sentiment:
                obj_dict["sentiment"] = sentiment.group(1)

            quotes_match = re.search(r"""["\']quotes["\']\s*:\s*\[\s*([^\]]*?)\s*\]""", obj_str, re.DOTALL)
            if quotes_match:
                quotes = []
                for qm in re.finditer(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''', quotes_match.group(1)):
                    q = qm.group(0).strip()[1:-1]
                    if q:
                        quotes.append(q)
                obj_dict["quotes"] = quotes

            if obj_dict:
                results.append(obj_dict)

        return json.dumps(results, indent=4), results
