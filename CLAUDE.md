# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Tube Alpha is a YouTube investment sentiment analysis tool. It scrapes financial YouTube videos, extracts transcripts, and uses OpenAI GPT-4o to identify mentioned assets (stocks, crypto, commodities) and their bullish/bearish sentiment — so users get a sentiment summary without watching the full video.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# One-time dev setup (creates DB schemas + dev user dev@example.com with 30-day pro)
python setup_dev.py

# Run development server
python main.py          # uvicorn on http://localhost:8000

# Run production server
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

## Architecture

### Request Flow

```
POST /api/v1/videos/process (YouTube URL)
  → VideoPipeline.process_video()
      1. Validate video ID
      2. Check DB cache (skip if already processed)
      3. Fetch metadata via proxy-rotated requests
      4. AI validation: is this an investing video?
      5. Fetch transcript (youtube-transcript-api + yt-dlp, proxy-rotated)
      6. Chunk transcript by token count (MAX_TOKENS_PER_CHUNK)
      7. Send chunks to OpenAI → extract asset/sentiment pairs
      8. Persist to data.sqlite (channels, transcripts, answers tables)
  → Return sentiment tiles to frontend
```

### Layer Structure

- **`tube_alpha/routers/`** — Thin FastAPI route handlers; minimal logic
- **`tube_alpha/services/`** — All business logic:
  - `pipeline.py` — Orchestrates end-to-end video processing
  - `sentiment.py` — OpenAI integration; chunks transcripts, parses LLM responses
  - `youtube.py` — Fetches transcript and video metadata
  - `scheduler.py` — Background asyncio task that periodically scrapes configured channels
  - `data.py` — Query layer for the frontend (video, asset, guest overviews)
  - `auth.py` — Extracts user email from `X-MS-CLIENT-PRINCIPAL` header (Azure AD B2C)
  - `proxy.py` — Rotates Webshare proxy users across requests
- **`tube_alpha/database.py`** — `Database` class wrapping SQLite; always use `fetch_all`, `fetch_one`, `fetch_scalar`, `execute` — never raw connections
- **`tube_alpha/config.py`** — Frozen `Settings` dataclass; reads `.env` + `config.yaml`
- **`templates/`** — Jinja2 HTML templates (no JS framework; vanilla JS in `static/`)

### Two Databases

- **`data/data.sqlite`** — Content: `channels` (video metadata), `transcripts` (raw chunks), `answers` (parsed sentiment items), `raw_answers` (raw LLM responses), `vw_assets` (view)
- **`data/admin.sqlite`** — Users: `users` table (email, pro_active, subscription dates)

Schema for both is defined in `schema.json` and applied via `Database.create_schema()`.

### Authentication

In `development` environment (`ENVIRONMENT=development` in `.env`), `AuthService` returns the hardcoded mock email `dev@example.com` — no Azure header required. In production it reads `X-MS-CLIENT-PRINCIPAL` (Azure AD B2C base64 JWT).

### Configuration

`config.yaml` controls which YouTube channels the scheduler scrapes and sentiment chunking behavior. `SENTIMENT.MAX_CHUNKS: -1` means use the full transcript; set to a positive integer to limit API calls during dev. Override via env `SENTIMENT_MAX_CHUNKS`.

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development` (default) or `production` |
| `OPENAI_API_KEY` | Required for sentiment extraction |
| `OPENAI_MODEL` | Defaults to `gpt-4o` |
| `WEBSHARE_PROXY_USERNAME/PASSWORD` | Webshare residential proxy credentials |
| `WEBSHARE_PROXY_USER_COUNT` | Number of proxy sub-users to rotate (default 5) |
| `AUTO_SCRAPE_ENABLED` | `1`/`true` to start background scheduler on app startup |
| `AUTO_SCRAPE_INTERVAL_HOURS` | Scheduler interval (default 6) |
| `SENTIMENT_MAX_CHUNKS` | Override transcript chunk limit from config.yaml |
| `LOG_LEVEL` | Logging verbosity (default `INFO`) |

## Logs

Each Python source module gets its own log file in `core/logs/` via `PerModuleFileHandler`. Logs also stream to stdout.
