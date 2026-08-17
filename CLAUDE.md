# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TalkProd-Chatbot is an AI shopping storefront for the Indian market (Amazon.in/Flipkart). A Flask backend serves a built React/Vite SPA and exposes JSON APIs for three features:

1. **Concierge search** (`gemini_service.get_top_products`) — grounded Google Search via Gemini returns 6 products; recovers real retailer URLs from grounding metadata and resolves `vertexaisearch` redirects.
2. **Talk to your product** (`product_knowledge.py`) — RAG over scraped Reddit discussions; answers grounded only on Reddit opinions.
3. **Price-watch agent** (`agent_service.py`) — a real tool-calling agent decides whether to email a shopper and when to re-check, driven by a background scheduler in `watch_service.py`.

## Commands

### Backend (Python, from repo root)
```bash
pip install -r requirements.txt
python backend.py                          # runs Flask dev server on :5000 (FLASK_DEBUG=1 by default)
python product_knowledge.py "Sony WH-1000XM5"   # standalone: ingest a product + test retrieval
python agent_service.py                    # standalone: run one price-watch agent turn against a fake watch
```
There is no test suite. Modules self-test via their `if __name__ == '__main__'` blocks (see `product_knowledge.py`, `agent_service.py`). `storefront-ui/e2e-talk.mjs` is a Playwright end-to-end script for the talk flow.

### Frontend (from `storefront-ui/`)
```bash
npm install
npm run dev        # Vite dev server on :5173, proxies /search /talk /watch to :5000 (see vite.config.js)
npm run build      # builds to storefront-ui/dist/ — Flask serves THIS, so rebuild after UI changes
```
Flask serves the built SPA from `storefront-ui/dist` ([backend.py](backend.py)). For UI changes to appear in the Flask-served app (not the Vite dev server), you must `npm run build`.

### Required environment (`.env`, gitignored)
`GEMINI_API_KEY`, `RAPIDAPI_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD` are required; `GEMINI_MODEL` (default `gemini-3.1-flash-lite`) and `CACHE_TTL_DAYS` (default 7) are optional. Missing SMTP creds disable email gracefully; missing API keys raise on first use.

## Architecture

### Concurrency model (important — read before changing request handling)
This is a **single-process** app on the Werkzeug dev server (threaded). Key consequences:
- **In-memory shared state**: ingestion job status (`backend._jobs`) and the ChromaDB client are held in process memory, guarded by locks (`_jobs_lock`). This only works because there is one process. Running multiple workers (gunicorn/waitress) would break job-status sharing and duplicate the scheduler — it would require moving `_jobs` to Redis and Chroma to server mode.
- **"Async" is manual threading, not asyncio**: `/talk/prepare` spawns a background `threading.Thread` for the slow scrape/embed and returns immediately; the React UI polls `/talk/status` (client-side `setInterval` in `storefront-ui/src/components/TalkDrawer.jsx`) until the job is `ready`. There is no `async`/`await` and no task queue.
- The price-watch **scheduler thread** is started only in the Werkzeug reloader's worker child (`WERKZEUG_RUN_MAIN` guard in [backend.py](backend.py)) to avoid duplicate emails. Under a real WSGI server this must move to an app factory.

### RAG pipeline (`product_knowledge.py`)
Scrape → chunk → embed → store → retrieve, entirely local/free except the Reddit API:
- **Scrape** via reddit3 RapidAPI (free plan ~100 req/month, tracked from response headers, hard-stops at 429). Comments are fetched only for the 8 most-discussed posts to conserve quota (one API call each).
- **Chunk** is rule-based: one chunk per post body + up to 5 top comments per commented post, junk-filtered and clipped to 1500 chars. ~40-70 chunks per product.
- **Store** in a persistent on-disk ChromaDB (`PersistentClient`, `.chroma/`), one collection per product (`talk-<slug>`), stamped with `ingested_at`. ChromaDB's default embedding function (all-MiniLM-L6-v2 via ONNX) embeds locally on CPU.
- **Reuse over re-scrape**: `ingest()` skips scraping if the collection is fresh (within `CACHE_TTL_DAYS`); `_cache_status()` returns fresh/stale/missing, treating an empty collection as missing (self-healing after a partial write).
- **Retrieve** returns a dynamic top-k: `TOP_K_FRACTION` (0.35) of the collection, floored at `MIN_TOP_K` (5) and capped at the total.

### Price-watch agent (`agent_service.py`)
A **LangChain `create_agent`** (LangGraph-backed ReAct loop) over `ChatGoogleGenerativeAI`. The agent is given a goal (the `_AGENT_BRIEF` system prompt) and 4 `@tool` closures over a per-run `state` dict: `get_current_price_tool`, `find_alternatives`, `notify_user`, `set_next_check_hours`. The model only *requests* tools; LangGraph dispatches to the Python functions and feeds results back until the model replies with no tool call. Loop is bounded by `RECURSION_LIMIT = 2 * MAX_STEPS + 2` (LangGraph counts ~2 supersteps per tool round). The grounded price/alternative lookups live in `gemini_service` and are only *called* by the tools, so LangChain never models the search tool itself.

### Service boundaries
- `email_service.py` is standalone (stdlib Gmail SMTP over SSL) so both `agent_service` and `watch_service` can send mail without a circular import; `send_email` never raises (returns False).
- `gemini_service.py` owns all grounded Google-Search calls and URL-recovery logic; both `backend` (search, talk answers) and `agent_service` (price/alternatives) depend on it.
- State persists to flat JSON files: `.quota.json` (RapidAPI quota), `.watches.json` (watches). Ingestion jobs are **not** persisted (in-memory only, by design).

### Model conventions
When building AI features here, default to the latest capable models. The Gemini model is set via `MODEL` in [gemini_service.py](gemini_service.py) (env-overridable). See the memory files under `.claude/` for project context and known gaps.
