# Release Notes — May 28, 2026

## Fix 6 Issues: Execution Gap, SHORT Bias, Conviction System, Feedback Loop, IBIT Removal, Inverse ETF Mapping

Closes Issues #8, #9, #13, #14, #15, #16.

**#8 — Remove IBIT from default watchlist (app_config.py):**

- IBIT removed from `DEFAULT_TRACKED_SYMBOLS` (81% loss rate, -16.44% total PnL since inception)
- Added `SUPPORTED_SYMBOLS` constant — IBIT stays available for manual re-addition via the Admin UI
- `MAX_TRACKED_SYMBOLS` now calculated from `SUPPORTED_SYMBOLS` so the cap is consistent

**#9 — AUTO-MAP SHORT signals to inverse ETFs (alpaca_broker.py, paper_trading.py):**

- Added `INVERSE_ETF_MAP` dictionary mapping individual tickers to their inverse ETFs (e.g., NVDA→PSQ, TSLA→SQQQ, SPY→SPXS, COIN→BITI)
- `maybe_execute_alpaca_order()` now buys the inverse ETF instead of skipping when short selling is disabled
- `process_signals()` in `paper_trading.py` sets `execution_ticker` to the inverse ETF at signal time so the dashboard shows the actual traded ticker
- `_configured_live_execution_symbols()` always includes all inverse ETF values so they pass the symbol allow-list check
- Added `test_inverse_etf_mapping.py` to cover SHORT→ETF routing

**#13 — Fix Execution Gap (91%→near 100%) (alpaca_broker.py, paper_trading.py):**

- Changed default `alpaca_execution_mode` from `"off"` to `"paper"`
- Added 1-retry logic (2s delay) to both `close_position()` and `place_order()` dispatch paths
- On final failure, logs to `alpaca_orders` and falls back to the decision log with full error context
- Added dispatch monitoring: per-trade log lines and a summary (`X closes, Y opens attempted`) in `_dispatch_alpaca_orders()`

**#14 — Eliminate SHORT Bias (94% SHORT) (logic_config.json):**

- Hardened SHORT entry thresholds: `bluster_short_threshold` -0.60→-0.70, `policy_signal_threshold` 0.40→0.45, `direction_confidence_min` 0.50→0.55
- Symmetrized technical modifiers: RSI overbought/oversold long/short penalties now ±0.06 (was -0.08), BB above/below penalties now ±0.03 (was -0.05)

**#15 — Fix Conviction System (all MEDIUM→25/50/25 distribution) (logic_config.json, persistence_service.py, paper_trading.py):**

- HIGH conviction: confidence ≥ 0.80 (confidence-based, regardless of score magnitude)
- LOW conviction: weak directional signal (`< 0.30`) AND low confidence (`< 0.55`)
- MEDIUM: everything else
- Added `low_holding_minutes` block in config for shorter holding windows on LOW conviction trades
- Removed the LOW conviction hard-block — LOW trades are now allowed at vol-scaled size

**#16 — Performance Feedback Loop (weekly Friday 4pm CT) (feedback_analysis.py, routers/feedback.py, models.py, main.py):**

- New `feedback_analysis.py` service: analyzes last 7 days of closed trades, detects patterns (direction bias, symbol underperformance, conviction mismatch, holding period losses), and generates parameter adjustment suggestions
- New `routers/feedback.py`: REST API — `GET /api/feedback/latest`, `POST /api/feedback/analyze`, `POST /api/feedback/accept`, `GET /api/feedback/schedule`, `GET /api/feedback/status`
- Added `DecisionLogFeedback` table to `models.py`
- Background scheduler in `main.py` checks every 30 minutes; fires analysis during the Friday 4–6pm CT window (once per week)

**Files changed:** `backend/config/logic_config.json`, `backend/database/models.py`, `backend/main.py`, `backend/routers/__init__.py`, `backend/routers/feedback.py` *(new)*, `backend/services/alpaca_broker.py`, `backend/services/analysis/persistence_service.py`, `backend/services/app_config.py`, `backend/services/feedback_analysis.py` *(new)*, `backend/services/paper_trading.py`, `backend/tests/test_inverse_etf_mapping.py` *(new)*, `backend/tests/test_paper_trading.py`

---

# Release Notes — May 22, 2026

## Live Position Stop-Loss/Take-Profit Monitoring + Client Order ID URL Encoding

Two improvements to the Alpaca execution path: live position risk monitoring and order lookup robustness.

**Stop-loss / take-profit monitoring before opens (alpaca_broker.py):**

- `maybe_execute_alpaca_order()` now checks existing live positions for stop-loss and take-profit triggers before allowing a new open
- Reads `stop_loss_pct` (default 2.0%) and `take_profit_pct` (default 3.0%) from config
- `_check_live_position_stop_loss()` and `_check_live_position_take_profit()` fetch the current Alpaca position, compute P&L percentage, and compare against thresholds
- If a stop-loss or take-profit is triggered, the new open is blocked and recorded in `alpaca_orders` with `status="skipped"` and a reason like `"stop_loss_hit: live position P&L -3.42%"`
- Prevents adding to positions that have already hit their risk limits

**Client order ID URL encoding (alpaca_broker.py):**

- `get_order_by_client_id()` now URL-encodes the client_order_id via `urllib.parse.quote(client_order_id, safe="")` before sending to Alpaca
- Prevents 400 errors when client_order_id contains special characters (e.g., dashes, spaces, or unicode in the `gr-{paper_id}-{event[:1]}-{timestamp}` format)

**Files changed:** `backend/services/alpaca_broker.py`

---

# Release Notes — May 20, 2026

## PDT Protection Fix + Underlying Conflict Resolution

Two fixes to the Alpaca execution path and paper trading simulation that were causing unnecessary trade blocks and conflicting positions.

**PDT protection relaxed for closes (alpaca_broker.py):**

- `_get_pdt_block_reason()` previously blocked same-day closes when `is_pdt_flagged` or `daytrade_count >= 3`
- Changed to always allow closes — PDT only restricts new openings, not selling what you own
- Added comment explaining the PDT rule clarification

**Extended-hours close uses `close_position()` endpoint (alpaca_broker.py):**

- Regular-hours closes now use Alpaca's `close_position()` endpoint which sells the exact remaining quantity including residual dust shares
- Extended-hours closes still compute qty manually since `close_position()` is unavailable pre/post-market
- Added success logging showing `order_id`, `status`, `qty`, `filled_qty`

**Underlying conflict resolution (paper_trading.py):**

- New `_resolve_underlying_conflicts()` function pre-scans all open positions and closes conflicting positions for the same underlying (e.g., SPXL bull + SPXS bear both open for SPY)
- Conflicts detected via opposite `signal_type` (LONG vs SHORT) or via execution tickers mapping to opposite buckets in `INSTRUMENT_SPECS`
- Older position is closed when a conflict is found
- Runs unconditionally — even when market is closed — to prevent positions from accumulating across weekend/overnight runs
- Also cleans up excess positions (>1 remaining for same underlying after conflict resolution)

**Order dispatch ordering (paper_trading.py):**

- `_dispatch_alpaca_orders()` now dispatches closes FIRST, then opens
- Ensures that when a direction flip occurs, the old position is fully closed before the new one is opened, preventing simultaneous opposing positions in the Alpaca account

**Files changed:** `backend/services/alpaca_broker.py`, `backend/services/paper_trading.py`

---

# Release Notes — May 12, 2026

## Alpaca Broker Fixes — Limit Price, Silent Return Bug, Error Visibility

Four fixes to the Alpaca order execution path that were preventing orders from being placed.

**Limit price not sent for regular-hours limit orders (alpaca_broker.py):**

- `place_order()` only included `limit_price` inside the `if extended_hours:` block. When `alpaca_order_type="limit"` during regular hours, the limit price was computed but never added to the HTTP payload, causing Alpaca to reject with `"limit orders require a limit price"` (code 40010001).
- Fixed by adding a universal `if limit_price is not None and order_type == "limit"` block after the extended-hours branch, so limit prices are sent for both regular and extended hours.
- `maybe_execute_alpaca_order()` now computes `entry_price ± slippage` for regular-hours limit opens, matching the existing extended-hours formula.

**Error response body discarded (alpaca_broker.py):**

- `_post()` called `r.raise_for_status()` which raised `httpx.HTTPStatusError`, but `str(exc)` only showed the status line — the actual Alpaca rejection reason in the response body was lost.
- Fixed by checking `r.status_code >= 400` before `raise_for_status()`, extracting `r.text[:2000]` into the exception message so the full Alpaca error is visible in logs and stored in the `alpaca_orders` table.

**Alpaca Order Success logging added (alpaca_broker.py):**

- Added console output on successful order placement showing `order_id`, `status`, `qty`, `filled_qty`, and `filled_avg_price`.
- Added debug prints for all previously silent return paths (execution mode check, broker initialization failure).

**Files changed:** `backend/services/alpaca_broker.py`

---

## Per-Symbol Conviction + Crazy Profile Alpaca Fix

Two fixes that ensure individual symbol signals are evaluated independently and that the crazy risk profile correctly bypasses conviction gates in the Alpaca broker.

**Per-symbol conviction (persistence_service.py):**

- Each symbol's conviction level is now computed from its own directional_score and confidence, not from the portfolio-level signal
- Previously, when opposing signals (e.g. SPY SHORT + USO LONG) produced a net-neutral portfolio HOLD, all symbols inherited LOW conviction — blocking both from paper trading and Alpaca execution
- Now SPY (directional=-0.576, confidence=0.772) gets MEDIUM conviction and USO (directional=0.58, confidence=0.758) gets MEDIUM conviction independently
- HOLD signals still get LOW conviction; directional signals use the standard or crazy-profile conviction thresholds
- Respects crazy profile conviction threshold overrides from logic_config.json

**Crazy profile Alpaca gate fix (alpaca_broker.py):**

- `_get_entry_conviction_block_reason` now accepts an optional `risk_profile` parameter
- When `risk_profile="crazy"`, LOW conviction entries are allowed through (matching the existing paper_trading.py behavior)
- The caller `maybe_execute_alpaca_order` passes `risk_profile` from config
- Previously, paper trades opened correctly with LOW conviction under crazy profile, but the Alpaca broker gate unconditionally blocked them

**Files changed:** `backend/services/alpaca_broker.py`, `backend/services/analysis/persistence_service.py`

---

# Release Notes — May 11, 2026

## Concurrent Analysis Fix + Multi-Leg Signal Preservation

Two bugs fixed in this release that were causing duplicate analyses and lost trading signals.

**Concurrency lock:**

- Added module-level `asyncio.Lock` in `pipeline_service.py` to serialize analysis pipeline runs
- Prevents the frontend's two auto-run components (page.tsx + AnalysisContext.tsx) from triggering duplicate analyses that would interleave Stage 1/Stage 2 output
- Lock is always released via try/finally on completion or error

**Multi-leg signal preservation:**

- When both long and short recommendations exist, uses net directional score to determine portfolio-level signal_type instead of picking a single winner
- All valid recommendations are preserved in the list for independent execution by the paper_trading engine and Alpaca dispatch
- Applied to both blue-team signal generation and red-team consensus

**Files changed:** `backend/services/pipeline_service.py`, `backend/services/signal_service.py`, `backend/services/paper_trading.py`, `backend/config/logic_config.json`, `frontend/src/app/admin/page.tsx`, `frontend/src/lib/utils/config-normalizer.ts`

---

## News Pulls Enabled for Custom Symbols

News pulls for custom quotes are now on by default. This improves the relevance of news articles for custom quotes, but may result in more news articles being pulled for users with a large number of custom quotes.

**Files changed:** `backend/services/app_config.py`, `frontend/src/components/admin/sections/OverviewSection.tsx`, `frontend/src/lib/utils/config-normalizer.ts`, `run.py`

---

## Data Ingestion Pass-Through for Low Keyword Match

When fewer than 10% of fetched articles have direct keyword matches for a symbol, the pipeline now passes all articles to the LLM instead of just the keyword matches. This prevents the pipeline from collapsing to 1 article when 332 were fetched — the proxy terms are intentionally narrow and may miss general market news that Stage 2's per-symbol specialist can still analyze.

- Added `MIN_COVERAGE_RATIO = 0.10` threshold in `engine.py`
- Added `content_hash` column to `scraped_articles` table for near-duplicate detection (same story, different URL)
- Added `processed_at` column for tracking when articles are processed
- Fast-lane analysis deferred to manual trigger to prevent scheduler from marking articles as processed before the stream endpoint runs
- Article loading changed from "new articles only" to "all unprocessed within age window" (default 12 hours) to make articles reusable across multiple analysis runs

**Files changed:** `backend/services/data_ingestion/worker.py`, `backend/services/sentiment/engine.py`, `backend/services/analysis/market_data_service.py`, `backend/routers/analysis.py`, `backend/database/models.py`, `backend/database/migrate.py`

---

## RSS Feed Changes — Removed NYT/WSJ

Default RSS feeds changed because NYT and WSJ are blocked by bot detectors, even with cookies passed.

**Files changed:** `backend/services/app_config.py`

---

## Trafilatura Article Extraction Fix + Feed Updates

The article extraction pipeline was silently failing for every RSS feed source. The root cause was a bug in `fetch_article_text()` that passed invalid parameters to `trafilatura.fetch_url()` — `favor_recall`, `include_comments`, and `include_tables` only exist on `trafilatura.extract()`, not `fetch_url()`. This raised a silent `TypeError` every time, so `extracted` was always `""` and articles were stored with only the RSS title + summary (~200 chars).

**What changed:**

- **Fixed extraction** — Changed from a single broken `trafilatura.fetch_url()` call to a two-step process: `requests.get()` to download the HTML, then `trafilatura.extract()` to parse the article body text. CNBC articles now return 1,988-5,349 chars of clean text (was 0). BBC returns 3,621-5,119 chars.
- **Removed Playwright** — Stripped all Playwright code (~200 lines) including `_fetch_with_playwright()`, `_scroll_page()`, `_dismiss_cookie_consent()`, `_to_playwright_cookies()`, and the `PLAYWRIGHT_FALLBACK_ENABLED` env var. Testing confirmed that MarketWatch, NYT, and FastCompany use DataDome bot protection that blocks even headless Chromium — Playwright was adding 30+ seconds of latency per article while never successfully extracting anything from protected sites.
- **Updated default RSS feeds** — Removed `nyt_business` (NYT blocks with DataDome) and `marketwatch` (blocks with DataDome). Added `npr_news` (NPR — 7,758 chars extracted ✅) and `techcrunch` (TechCrunch — 3,826 chars extracted ✅).
- **Reusable feed tester** — New `test_rss_feed_compatibility.py` script lets users test any article URL before adding it as a custom feed. Run `python test_rss_feed_compatibility.py <url>` — returns PASS/BLOCKED/LOW_CONTENT/ERROR with recommendations.

**Files changed:** `backend/services/data_ingestion/worker.py`, `backend/services/app_config.py`, `test_rss_feed_compatibility.py` (new)

---

# Release Notes — May 10, 2026

## Ollama URL Support for Remote Instances

The Ollama backend URL configuration now supports both local and remote Ollama-compatible endpoints. Previously the URL was treated as strictly local. Users can now point the backend at Ollama instances running on other machines or cloud-hosted Ollama-compatible servers.

**Files changed:** `backend/services/app_config.py`


---

# Release Notes — May 9, 2026

## Comprehensive Decision Logging

A new `decision_log.db` (separate SQLite file to avoid lock contention with the main trading DB) records every analysis decision in detail. This provides a complete audit trail of the reasoning pipeline.

**9 new SQLAlchemy models in the decision log:**

- `DecisionLogRun` — one row per full analysis run (timestamps, models used, config hash, article counts)
- `DecisionLogSymbol` — one row per symbol per run (blue/red team scores, raw/blended scores, final signal, decay factors, regime adaptation state, materiality gate state)
- `DecisionLogArticle` — articles considered per symbol (title, source, published_at, was_used, relevance_score)
- `DecisionLogBlend` — prior runs that contributed to blended scores (prior run timestamp, weight, directional score)
- `DecisionLogTechnical` — technical indicators evaluated per symbol
- `DecisionLogTrade` — paper trade entries with size/leverage reasoning, holding window
- `DecisionLogTradeEvent` — lifecycle events per trade (open, trailing_stop_set, close)
- `DecisionLogDecisionDiff` — mid-position decision changes

**Pipeline integration:**

- `log_run_start` at analysis begin, per-symbol scores after signal generation, `run_complete` at end
- Paper trading integration: trade entry logging with size/leverage reasoning, trailing stop set events, close events with P&L
- `config_hash` (SHA256) computed from config values for version correlation
- `decision_log.db` added to `.gitignore`

**Files changed:** `backend/services/decision_logger.py` (new), `backend/services/paper_trading.py`, `.gitignore`

---

## Cloud Model Softening + Max Tokens Increase

Cloud-hosted models (deepseek, gemma, etc.) tend to default to conservative "noise"/"UNRELATED" classifications even for articles that passed Stage 1 keyword filtering. Two fixes address this:

**`SYMBOL_SPECIALIST_LEAN_HEADER` softening:**

- "skeptical financial news analyst" → "thorough financial news analyst"
- "Red-team stance" → "Analysis stance"
- Added context: "Stage 1 keyword filtering confirmed this article contains terms relevant to {symbol}"
- Added: "This is a quantitative analysis tool, not financial advice"
- Explicit instruction: "Only use 'noise' for event_type if the article is truly about sports, entertainment, or celebrity news"

**Cloud-model overrides in `compute_symbol_scores()`:**

- When `cloud_backend=True` and `event_type="noise"` with zero substance/bluster counts, defaults to `event_type="macro_data"` (Stage 1 confirmed relevance)
- When `cloud_backend=True` and `exposure_type="UNRELATED"`, downgrades to `BROAD` and marks as relevant (Stage 1 confirmed topic relevance)

**Max tokens increased:**

- `MAX_TOKENS` raised from 2048 → 8192 to prevent truncated analyses that produce wrong signals

**OpenAI client improvements:**

- Attempts `json_schema` (structured output) first, falls back to `force_json` if provider doesn't support it
- Sends entire prompt as a single user message (matching how Ollama receives it) to fix cloud model output classification

**Files changed:** `backend/services/sentiment/engine.py`, `backend/services/sentiment/prompts.py`, `backend/services/openai_client.py`

---

## Cloud/Local Toggle — Reworked Provider Selection

The old 3-card backend selector (Ollama / vLLM / Cloud LLM) has been replaced with a two-button Cloud/Local toggle. This simplifies the mental model: choose *where* inference runs, then pick the *specific provider* within that mode.

**UI changes:**

- Two large toggle buttons at the top of the LLM Configuration section: **☁️ Cloud** and **🖥️ Local**
- Cloud mode providers: OpenRouter, Anthropic, OpenAI, Google, Custom
- Local mode providers: Ollama, vLLM, llama.cpp, Custom
- Each mode has its own URL smart-fill map — switching providers auto-populates the URL
- **Protocol validation** enforces `https://` for cloud and `http://` for local, with red borders and inline warnings
- Custom URL editing is tracked via a `user_edited_url` flag; provider changes always smart-fill regardless of edit history, and an amber warning explains the behavior
- A "custom" badge appears next to the URL when the user has manually edited it

**Conditional UI sections:**

- **Cloud mode**: model dropdown (auto-populated from provider), API key management, connection test, env fallback note
- **Local mode**: per-provider docs panel (Ollama's `/api/generate`, vLLM's `/v1/completions`, etc.), connection test, env fallback note

**Backward compatible state mapping:**

- Cloud → `inference_backend = "openai"`, model via `openai_model`
- Local (Ollama) → `inference_backend = "ollama"`
- Local (vLLM/llama.cpp/custom) → `inference_backend = "vllm"`
- Existing `lastLocalModelsRef` logic for model switching preserved

**Files changed:** `frontend/src/components/admin/sections/CloudLLMSection.tsx`, `frontend/src/app/admin/page.tsx`, `frontend/src/lib/utils/config-normalizer.ts`

---

## Per-Provider API Keys

Each cloud provider now stores its own API key in the OS keychain under a separate slot. Switching providers shows the correct key's status immediately.

| Provider | Keychain Slot |
|---|---|
| OpenAI | `openai_api_key` (legacy, backward compatible) |
| Anthropic | `anthropic_api_key` |
| OpenRouter | `openrouter_api_key` |
| Google | `google_api_key` |
| Custom | `custom_api_key` |

**What changed:**

- `secret_store.py` — Added `get_cloud_api_key(provider)`, `save_cloud_api_key(provider, key)`, `clear_cloud_api_key(provider)`. Legacy functions preserved as wrappers.
- Backend `/admin/openai-secrets` endpoints — Accept `?provider=` query parameter. Defaults to "openai" when omitted.
- Frontend passes `?provider=` when fetching/saving/clearing keys. Re-fetches secret status when provider changes.
- Sentiment engine uses `get_cloud_api_key(cloud_provider)` to read the right provider's key. Only reads the key when `inference_backend == "openai"` (cloud mode) — local mode never loads any cloud API key.
- Pipeline → sentiment service → engine: `cloud_provider` forwarded through the chain from `config.cloud_provider`.

**Files changed:** `backend/services/secret_store.py`, `backend/routers/config.py`, `backend/services/sentiment/engine.py`, `backend/services/analysis/sentiment_service.py`, `backend/services/analysis/pipeline_service.py`, `frontend/src/components/admin/sections/CloudLLMSection.tsx`, `frontend/src/app/api/admin/openai-secrets/route.ts`

---

## DB Persistence for New UI Fields

The new Cloud/Local toggle state (`api_mode`, `cloud_provider`, `local_provider`) is now persisted in the database, so the mode survives restarts without needing to toggle back.

- Three new columns on `app_config`: `api_mode` (VARCHAR 16), `cloud_provider` (VARCHAR 32), `local_provider` (VARCHAR 32)
- Migration added to `migrate.py` — runs automatically on next backend restart
- `update_app_config` and `config_to_dict_with_stats` handle the new fields

**Files changed:** `backend/database/models.py`, `backend/database/migrate.py`, `backend/services/app_config.py`

---

## Smart Default Models per Provider

The model dropdown now auto-selects the best inexpensive model for each provider when cloud models first load or when the provider changes.

| Provider | Preferred Defaults |
|---|---|
| OpenRouter | `deepseek/deepseek-r1` → `deepseek/deepseek-chat` → `mistralai/mistral-7b-instruct` |
| OpenAI | `gpt-4o-mini` → `gpt-4o` → `gpt-4.1-nano` |
| Anthropic | `claude-3-5-haiku-20241022` → `claude-3-haiku-20240307` |
| Google | `gemini-2.0-flash-lite` → `gemini-2.5-flash-preview-04-17` → `gemini-1.5-flash` |

Auto-selection only triggers when the current model is still the generic `gpt-4o-mini` — a model already chosen by the user is never overwritten.

**Files changed:** `frontend/src/components/admin/sections/CloudLLMSection.tsx`

---

## Security Hardening

- **Refuse 0.0.0.0 without ADMIN_API_TOKEN** — The backend now raises a hard `RuntimeError` at startup if `HOST=0.0.0.0` is set without also setting `ADMIN_API_TOKEN`. Previously this was only a warning.
- **Provider domain validation** — When editing a cloud provider's URL, an amber warning appears if the hostname doesn't match the expected domain (e.g. `api.openai.com` vs `openrouter.ai`).
- **Protected analysis mutation endpoints** — `POST /analyze`, `POST /analyze/stream`, `POST /analysis-snapshots/{id}/rerun`, and `POST /paper-trading/expire-check` now require the admin token. Frontend proxy routes forward `X-Admin-Token` conditionally.
- **`POST /analyze` fix** — The stale `inference_backend` was causing cloud API keys to be sent to local endpoints when switching modes without a config save. The engine now only reads API keys when the backend is "openai".

**Files changed:** `backend/main.py`, `backend/routers/analysis.py`, `frontend/src/app/api/analyze/route.ts`, `frontend/src/app/api/analyze/stream/route.ts`, `frontend/src/app/api/analyze/rerun/route.ts`

---

## Connection Test Buttons

Both cloud and local modes now have a "🔌 Test Connection" button that verifies the endpoint is reachable before running an analysis.

- **Cloud mode**: Calls `POST /admin/openai-test-connection` → pings the provider's `/v1/models` endpoint and tests inference with a minimal prompt. Shows model count and inference verification status.
- **Local mode**: Calls `GET /api/ollama/status` → pings Ollama's `/api/tags` endpoint. Shows available models and the active model name.
- The test connection button in cloud mode accepts optional `base_url` and `provider` parameters so it works even before the config is saved.

**Files changed:** `backend/routers/config.py`, `frontend/src/app/api/admin/openai-test-connection/route.ts` (new), `frontend/src/components/admin/sections/CloudLLMSection.tsx`

---

## `.env.example`

A new `.env.example` at the repo root documents all 20 supported environment variables with descriptions and defaults. Copy it to `.env` and edit as needed instead of hunting through source code.

**Files changed:** `.env.example` (new)

---

## Telegram Bot Hot-Reload

The Telegram bot loop now re-reads credentials from the OS keychain on every poll cycle — updating credentials in the admin UI takes effect without a backend restart. When credentials are missing, the loop backs off and retries instead of exiting.

The boot logic always starts the bot loop when `remote_control_enabled` is true, even if credentials are absent at startup. The loop automatically starts polling once credentials appear.

**Files changed:** `backend/main.py`, `backend/routers/config.py`

---

## Cloud Model Fetch Fix

When fetching available models for the cloud provider dropdown, the frontend now passes both `?base_url=` and `?provider=` as query parameters. This lets the backend use the correct API URL and per-provider API key from the keychain even before the user has saved the config. Previously the model fetch used the DB-stored `openai_base_url` (still the default `https://api.openai.com/v1`) and the legacy `openai_api_key` slot, which would fail for any provider other than OpenAI.

**Files changed:** `backend/routers/config.py`, `frontend/src/app/api/admin/models/route.ts`, `frontend/src/components/admin/sections/CloudLLMSection.tsx`

---

## Stale State Fix on Mode Toggle

Fixed a race condition where switching from local to cloud mode would fail to fetch cloud models because the `secrets.configured` flag was still the stale local-mode value. The fix removes the `secrets.configured` gate from the model fetch trigger — the backend handles auth internally — and resets stale cloud models + errors when entering cloud mode.

**Files changed:** `frontend/src/components/admin/sections/CloudLLMSection.tsx`



---

# Release Notes — May 7, 2026

The auto-run analysis timer now runs regardless of which page the user is on. Previously, navigating to the Trading, Admin, or Health pages would stop the countdown because the timer lived inside the main dashboard component, which unmounts on navigation.

**AnalysisProvider context (`frontend/src/lib/context/AnalysisContext.tsx`):**

- New React Context + Provider wraps the root layout so the countdown timer and analysis trigger persist across all pages
- Fetches config on mount and every 60 seconds to stay in sync with admin changes
- Runs the countdown timer via `useEffect` with `setInterval` at the layout level — never unmounts
- When countdown hits 0, fires a POST to `/api/analyze/stream` and consumes the full SSE stream, capturing the `result` event
- Stores the latest completed analysis result in `latestResult` via context
- Exposes `countdown`, `isAnalyzing`, `config`, `configLoaded`, `triggerAnalysis()`, `resetCountdown()`, and `latestResult`

**Auto-run results become the hero signal:**

- The main dashboard watches `latestResult` from context via a `useEffect`
- When a new auto-run completes (even if the user was on the Trading page), the result is promoted to the hero signal on the next visit
- The request_id is saved to localStorage so the History tab marks it as CURRENT

**Persistent countdown indicator (`frontend/src/components/AutoRunCountdown.tsx`):**

- A small fixed-position badge in the bottom-right corner shows the countdown on every page
- Displays "Auto-run in MM:SS" or "Analyzing..." depending on state
- Hidden when auto-run is disabled in admin settings

**Files changed:** `frontend/src/lib/context/AnalysisContext.tsx` (new), `frontend/src/components/AutoRunCountdown.tsx` (new), `frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`

---

## Run-to-Run Diff in History Tab

The History tab now shows signal flips and data gaps inline on each snapshot row instead of a separate diff section.

**`frontend/src/components/Dashboard/PullHistoryCard.tsx`:**

- Each snapshot row compares its signal_type to the next older run
- A **SIGNAL FLIP** badge appears when the signal changed (e.g., "SIGNAL FLIP: LONG → HOLD")
- A **DATA GAP** badge appears when article count dropped significantly (≥5 drop to ≤3 articles)
- The list stays in its original newest-first chronological order

---

## Data Gap Protection (HOLD-with-Momentum)

When the article count drops by ≥60% from the previous run (and the previous run had ≥10 articles), the system now preserves open positions instead of closing them on a transient HOLD signal.

**`backend/schemas/analysis.py` — `TradingSignal.data_gap_hold`:**

- New boolean field on the TradingSignal schema, default `false`
- Set to `true` when the signal is HOLD and article count dropped significantly

**`backend/services/analysis/signal_service.py` — `generate_trading_signal()`:**

- Accepts `previous_posts_count` and `current_posts_count` parameters
- After computing the signal, checks if article count dropped by ≥60% from a previous baseline of ≥10
- If so, sets `data_gap_hold: true` on the returned TradingSignal

**`backend/services/analysis/pipeline_service.py` — `run_stream()`:**

- Extracts `previous_posts_count` from the previous analysis state (via `HysteresisService`)
- Passes both `previous_posts_count` and `current_posts_count` to the signal generator

**`backend/services/paper_trading.py` — `process_signals()`:**

- When processing a HOLD recommendation, checks for `data_gap_hold: true`
- If data_gap_hold is active and a position exists, skips closing it and logs "HOLD (data gap — preserving position)"
- The position stays open until the next run with adequate data either confirms the HOLD (without the flag) or produces a new directional signal

**`frontend/src/components/Dashboard/SignalHero.tsx`:**

- Shows "HOLD (insufficient data)" instead of just "HOLD" when `data_gap_hold` is true
- Displays an orange warning: "Article count dropped significantly from the previous run. Positions are preserved until adequate data returns."

---

## Rolling Sentiment Averaging

Blends the current run's per-symbol sentiment scores with recent historical runs using exponential decay. This prevents a single run of noisy or sparse articles from flipping the trading signal.

**`backend/services/analysis/rolling_sentiment.py` (new):**

- `load_recent_scores(db, symbols, max_age_hours=2.0)` — loads all analysis runs within the last 2 hours from the existing `analysis_results` table (no new DB tables needed)
- `blend_with_history(current_scores, historical_runs, half_life_hours=0.33)` — blends scores using exponential decay with a 20-minute half-life
- Non-numeric fields (reasoning, signal_type, urgency) are preserved from the current run, not blended
- Works with any run frequency (10 min, 30 min, etc.) — the decay formula handles it automatically

**`backend/services/analysis/pipeline_service.py` — `run_stream()`:**

- Calls `load_recent_scores` and `blend_with_history` after sentiment analysis
- Passes the blended scores to the signal generator instead of raw single-run scores

**Stabilization effect (20-min half-life, 10-min schedule):**

- Current run: 100% weight
- 10 min ago: 70% weight
- 20 min ago: 50% weight
- 30 min ago: 35% weight
- 40 min ago: 25% weight
- 50 min ago: 18% weight
- 60 min ago: 12% weight

A 1-article data gap run with near-zero scores would only move the blended average by ~10-15%, so the signal stays stable.

**Files changed:** `backend/services/analysis/rolling_sentiment.py` (new), `backend/services/analysis/pipeline_service.py`, `backend/services/analysis/signal_service.py`, `backend/schemas/analysis.py`, `backend/services/paper_trading.py`, `frontend/src/components/Dashboard/SignalHero.tsx`, `frontend/src/components/Dashboard/PullHistoryCard.tsx`

---

# Release Notes — May 7, 2026

## Cloud LLM Support — OpenAI-Compatible Inference Backend

This release adds first-class support for OpenAI and any OpenAI-compatible cloud provider as an inference backend alongside Ollama and vLLM. Users can now run the analysis pipeline against cloud models like GPT-4o, GPT-4o-mini, or any provider with an OpenAI-compatible chat completions API — all configured from the Admin UI.

**Three inference backends:**

- **Ollama** (default) — local GPU inference via Ollama's `/api/generate` endpoint
- **vLLM** — local OpenAI-compatible servers via `/v1/completions`
- **Cloud LLM** — any OpenAI-compatible cloud API via `/v1/chat/completions`

The backend selector is a first-class setting in the Admin UI's **LLM Configuration** section. Switching backends requires no restart — the change takes effect on the next analysis run.

**OpenAI-compatible client (`backend/services/openai_client.py`):**

- Wraps the OpenAI Chat Completions API into the same `{"response": "..."}` envelope expected by the existing sentiment engine — all downstream JSON repair, schema validation, and scoring logic works unchanged
- Supports JSON Schema via `response_format` (OpenAI structured outputs) and `force_json` via `{"type": "json_object"}`
- Private IP address detection for local servers: allows HTTP for LAN endpoints, requires HTTPS for public cloud providers
- URL normalization strips common path suffixes (`/v1/chat/completions`, `/v1/completions`, etc.) so users can paste full endpoint URLs
- Robust error handling with specific messages for auth failures (401), model-not-found (404), timeouts, and connection errors
- Chat message construction heuristic: splits raw prompts into system + user messages for better instruction following

**API key management:**

- Cloud LLM API keys are stored in the OS keychain via `keyring` (Windows Credential Manager / macOS Keychain Access) — never in the repo or frontend bundle
- Keys can be saved, tested, and cleared from the Admin UI
- Falls back to `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` environment variables when keychain values are not set
- The Admin UI shows a masked key prefix with a green "Configured" badge when a key is stored

**Admin UI — LLM Configuration section (`CloudLLMSection.tsx`):**

- Three-card backend selector (Ollama / vLLM / Cloud LLM) with descriptions and taglines
- Cloud LLM settings: base URL input, default model dropdown (fetches available models from the provider), API key save/clear with password-masked input
- **Load models** button that queries the provider's `/v1/models` endpoint and populates a combined local + cloud model dropdown
- Per-stage model overrides note directing users to the Model Orchestration section for separate Stage 1 and Stage 2 models
- Advanced Mode shows environment variable fallback documentation
- Status messages for save/clear operations and model loading errors

**Per-stage model orchestration integration:**

- The Model Orchestration section's Stage 1 and Stage 2 model selectors now include both local models (from Ollama/vLLM) and cloud models (from the OpenAI-compatible provider) in a single combined dropdown
- Models are tagged with `(local)` or `(cloud)` prefixes for clarity
- The `inference_backend` config field controls which provider handles all model requests

**Sentiment engine provider dispatch:**

- `SentimentEngine._call_ollama_sync` dispatches to `_call_openai_sync` or `_call_vllm_sync` based on the `inference_backend` setting
- Cloud backends (OpenAI / vLLM) handle concurrency natively — the Ollama semaphore is bypassed for cloud calls
- The OpenAI sync path always uses `force_json=True` and never sends `response_schema` (json_schema response_format), because many non-OpenAI providers (OpenRouter, Together, etc.) do not support strict JSON schema mode. The existing JSON repair pipeline handles any formatting deviations.

**SSRF protection:**

- `_validate_base_url` in `openai_client.py` blocks HTTP connections to public IP addresses — only HTTPS is allowed for cloud endpoints
- HTTP is permitted for private/reserved IP ranges (127.0.0.0/8, 10.0.0.0/8, 192.168.0.0/16, etc.) so local vLLM/TGI servers work without TLS
- DNS resolution is checked at runtime; unresolvable hosts are conservatively blocked

**Files changed:** `backend/services/openai_client.py` (new), `backend/services/sentiment/engine.py`, `backend/services/secret_store.py`, `backend/services/app_config.py`, `backend/database/models.py`, `backend/database/migrate.py`, `backend/routers/config.py`, `backend/routers/analysis.py`, `frontend/src/components/admin/sections/CloudLLMSection.tsx` (new), `frontend/src/app/admin/page.tsx`, `frontend/src/lib/utils/config-normalizer.ts`, `frontend/src/lib/types/analysis.ts`, `README.md`, `RELEASENOTES.md`

---

## Strategy Feature Toggles: Continuous Entry, Regime Adaptation, and Hold Decay

Three new DB-backed toggles give admin users control over individual strategy features without editing `logic_config.json`. Each toggle is **global** (applies identically across Conservative / Standard / Crazy / Custom risk profiles) and null means "use the `logic_config.json` default."

**Continuous Entry Sizing** — When enabled (default), entry/exit is computed via sigmoid on directional score: at midpoint=0.42 the position is half-sized, tapering off below skip_floor=0.10. Existing positions smoothly shrink rather than flipping between 100% and 0%. Disabling reverts to the legacy binary gate (100% or nothing).

**Regime Adaptation** — When enabled (default), the entry threshold is dynamically adjusted based on market volatility. ATR% above the high-vol threshold multiplies entry_threshold by 1.25 (harder to enter), and below the low-vol threshold multiplies by 0.80 (easier to enter). Disabling uses a static threshold regardless of volatility.

**Separate Hold Decay** — When enabled (default off in default config), positions already held use a slower decay half-life than new entries, preventing existing positions from decaying too quickly under stale news. Disabling uses the same decay half-life for both entry and hold.

**Admin UI** — All three toggles appear in the Custom Risk Profile modal with a note: "These apply globally to all risk profiles. Off = use logic_config.json default."

**Files changed:** `backend/database/models.py`, `backend/database/migrate.py`, `backend/services/app_config.py`, `backend/services/analysis/signal_service.py`, `backend/services/analysis/pipeline_service.py`, `backend/routers/analysis.py`, `frontend/src/lib/utils/config-normalizer.ts`, `frontend/src/components/admin/modals/CustomRiskModal.tsx`

---

## Security Audit — Rate Limiting, SSRF Protection, Audit Logging, and More

A full security audit was run against the codebase, surfacing 14 issues across three severity levels. All high and medium concerns were addressed.

**Why this matters:** This app can route real money to Alpaca. Without basic protections, a compromised dependency, a malicious RSS feed, or even a misbehaving browser extension on localhost could alter trading behavior or leak API keys. These changes lock down the attack surface while keeping the local-first workflow intact.

**What changed:**

- **Rate limiting added** — Every API endpoint is now capped at 60 requests per minute. Previously, an attacker (or a runaway script) could hammer the backend as fast as the network allowed. This prevents brute-force token guessing and accidental resource exhaustion. The limit is generous enough that normal dashboard polling is unaffected.

- **Admin token warnings strengthened** — When `ADMIN_API_TOKEN` is not set, the backend now prints a bold startup warning: *"Sensitive routes (config, Alpaca, trades) are UNPROTECTED."* Previously the message was easy to miss. The token itself is still optional by design (this is a local-first tool), but the risk is now impossible to ignore.

- **CORS hardened** — If `CORS_ORIGINS` is set to `*` (wildcard), the backend now automatically disables credential sharing. The old configuration would allow any website to make authenticated requests — a dangerous combination. Specific origins like `http://localhost:3000` still work with full credentials.

- **SSRF protection for RSS feeds** — Custom RSS feed URLs are now checked against private IP ranges (127.0.0.0/8, 10.0.0.0/8, 192.168.0.0/16, 169.254.0.0/16, and IPv6 equivalents) before the backend fetches them. If a feed URL resolves to a private address, it is silently rejected. This prevents an attacker who can modify feed settings from using the backend to probe internal networks or scrape cloud metadata endpoints (the classic SSRF attack).

- **Security headers on every response** — The backend now sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy`, and `Referrer-Policy` on all HTTP responses. These headers prevent clickjacking, MIME-type sniffing, and referrer leakage — standard web security that was previously missing.

- **Request body size limit** — Payloads larger than 10 MB are now rejected with a 413 status. Prevents memory exhaustion from oversized uploads.

- **Audit logging** — A new `audit_log` database table records every config change and data reset with a timestamp, action type, and before/after context. Previously there was no record of who changed what or when. Config updates and data resets are now traced. The audit log is append-only and survives data resets.

- **SQLite file permissions** — On Unix systems, the database file is now locked to owner-only read/write on every connection. Prevents other local users from reading trade history or config.

- **Telegram bot exponential backoff** — If the Telegram API becomes unreachable, the bot now waits 1 second, then 2, 4, 8, up to 60 seconds before retrying. Previously it retried every 5 seconds unconditionally, which could trigger rate limiting or an IP ban.

- **Graceful shutdown timeout** — Background tasks (ingestion, polling, Telegram) now have a 5-second timeout during shutdown. Previously they could block indefinitely, preventing clean restarts.

**Files changed:** `backend/main.py`, `backend/security.py`, `backend/services/app_config.py`, `backend/database/engine.py`, `backend/database/models.py`, `backend/services/audit_log.py` (new), `backend/routers/config.py`, `requirements.txt`

**New dependency:** `slowapi>=0.1.9` — run `pip install -r requirements.txt` to pick it up.

---

## Live P&L Calculations, Manual Close Button, and Live Summary Endpoint

Two follow-up releases corrected persistent P&L discrepancies in live Alpaca trading and added user-initiated position management.

**Live P&L calculation fixes:**

- Realized P&L on closed live trades is now computed from Alpaca's actual fill prices and quantities rather than deriving it from paper-trade entry prices — paper and live prices diverge when fills differ
- Open position P&L uses Alpaca's current market value and cost basis directly from the brokerage API instead of estimating from entry price and last trade
- Fixed a divide-by-zero edge case in the annualized return calculation when realized P&L is exactly -100% (total loss)
- Live open positions now display actual Alpaca position data (`qty`, `market_value`, `cost_basis`, `unrealized_pl`) rather than deriving positions from the order history, which was inaccurate for multi-fill opens and partial closes

**Manual close button:**

- Each open live position in the trading page now has a **Close** button that sends an immediate market sell/cover order to Alpaca
- The close records the action in the audit log with the reason "manual close"
- After closing, the position card updates to show the closed state without requiring a page refresh

**Live summary endpoint:**

- New `/alpaca/live-summary` API endpoint returns aggregated live trading data: total realized P&L, total realized win rate, average win/loss, open P&L, deployed capital, and per-position details
- Replaces ad-hoc client-side calculations with a single server-authoritative call
- The trading page consumes this endpoint for the live summary cards and position table, keeping P&L numbers consistent with what Alpaca reports

**Files changed:** `backend/services/alpaca_broker.py`, `backend/routers/alpaca.py`, `frontend/src/app/trading/page.tsx`, `backend/routers/analysis.py`, `backend/services/analysis/persistence_service.py`, `frontend/src/app/api/alpaca/live-summary/route.ts` (new)

---

# Release Notes — May 4, 2026

## Full vLLM Backend Support and Admin UI Integration

This release added first-class vLLM support alongside Ollama, enabling users to run the analysis pipeline against any OpenAI-compatible model server.

**vLLM service layer:**

- New `backend/services/vllm.py` provides model discovery via `/health` and `/v1/models` endpoints, plus status reporting with reachability, active model name, and available model list
- The service returns a status payload that mirrors the existing Ollama status shape — the frontend health/status components work unchanged with either backend
- Configured via `VLLM_URL` environment variable (defaults to `http://localhost:8000`)

**Sentiment engine provider abstraction:**

- The sentiment engine (`backend/services/sentiment/engine.py`) was refactored to dispatch model requests to either Ollama or vLLM based on the resolved endpoint URL
- Request construction, header handling, and response parsing share a common code path regardless of provider
- The existing `OLLAMA_MODEL` environment variable is used as the backend-agnostic configured model name

**Admin UI vLLM status:**

- The Admin models section now shows vLLM status alongside Ollama: reachability indicator, active model, and available model list
- When both providers are reachable the user sees both, making provider choice transparent from the dashboard
- Status polling updates live without requiring a page refresh

**Trading page improvements:**

- Improved error handling when live trade data is unavailable or stale — the page degrades gracefully instead of showing broken cards
- Added more detailed logging throughout the trading page data pipeline for easier debugging
- Refactored trading page component code for better maintainability

**Files changed:** `backend/services/vllm.py` (new), `backend/services/sentiment/engine.py`, `backend/services/ollama.py`, `backend/services/app_config.py`, `backend/services/paper_trading.py`, `backend/database/migrate.py`, `backend/database/models.py`, `backend/routers/analysis.py`, `backend/services/analysis/persistence_service.py`, `backend/main.py`, `frontend/src/app/admin/page.tsx`, `frontend/src/app/trading/page.tsx`

---

# Release Notes — May 3, 2026

## Telegram Hardening, Split Admin Controls, and Telegram-Only Snapshots

Telegram snapshot delivery and Telegram bot control were tightened and separated into clearer admin-facing controls.

**Telegram credentials and verification:**

- Telegram setup now stores three values in the OS keychain: `bot token`, `private chat id`, and `authorized user id`
- Added built-in Telegram verification from Admin so the app can confirm the bot token works and the saved IDs point to the same private 1:1 chat
- Added an in-app Telegram setup help modal with BotFather and user-ID instructions

**Remote control hardening:**

- Telegram remote control is now bound to exactly one private chat and one authorized Telegram user
- Group chats and channels are intentionally rejected
- The bot only supports `/status`, `/stop`, `/start`, and `/help`
- `/status` reports the current Alpaca execution mode
- `/stop` switches Alpaca execution mode to `off` and stores the previous mode for later resumption
- `/start` restores the previously saved execution mode after `/stop`
- Backlogged Telegram updates are discarded on startup so stale `/start` or `/stop` messages do not replay after a restart
- Remote Telegram errors now return generic failure messages to chat while detailed exceptions stay in backend logs

**Admin UI changes:**

- The old single Telegram setup flow was split into two independent feature toggles:
  **Remote Snapshots** and **Remote Control**
- Saving Telegram credentials no longer silently enables snapshot delivery
- The system/Telegram area in Admin now shows the supported bot commands with clear descriptions of what they do and do not do
- Left-rail admin wording was updated so the Telegram/system area more clearly covers both snapshots and remote control

**Remote snapshots simplified:**

- Removed Signed Link and Email snapshot delivery options from Admin and backend routing
- Remote snapshot delivery is now explicitly Telegram-only
- `remote_snapshot_mode` remains in config/storage for compatibility, but normalizes to `telegram`

**Files changed:** `backend/services/telegram_bot.py`, `backend/services/secret_store.py`, `backend/routers/config.py`, `backend/main.py`, `backend/services/remote_snapshot.py`, `backend/services/app_config.py`, `backend/database/models.py`, `backend/database/migrate.py`, `frontend/src/app/admin/page.tsx`, `frontend/src/components/admin/modals/RemoteSnapshotSetupModal.tsx`, `frontend/src/components/admin/sections/RemoteSnapshotSection.tsx`, `frontend/src/lib/utils/config-normalizer.ts`, `README.md`

---

## Admin UI Redesign, Order Sizing Toggle, and Domain Cookie Injection

### Admin UI redesign

The admin page was rewritten from a single long scroll into a section-based layout with a persistent left sidebar for navigation. Sections are: Overview, Models, Trading Logic, Symbols, RSS Feeds, Prompt Overrides, Scheduling & System, Remote Snapshot, Price History, and Execution & Brokerage.

**Layout and navigation:**
- Sidebar shows all sections; clicking jumps to the selected panel — no more page-length scrolling
- Advanced Mode toggle in the sidebar cleanly separates the mode indicator ("✓ Advanced mode") from the switch action; previously these concatenated into an unreadable "✓ Advanced ModeSwitch to Basic" string
- Broken "Jump to section" dropdown that called `scrollIntoView` on non-existent DOM IDs was removed entirely

**Execution & Brokerage section reorganized:**
- Execution destination (Off / Alpaca Paper / Alpaca Live) at the top
- Live order limits appear immediately below — and only when live trading is active, so paper traders never see them
- "Dollar size of each live order placed" was renamed to **Live order baseline ($)** with a description clarifying it scales ×0.25–×5; a computed range hint ("Actual range: $X – $Y") appears live as you type
- Paper order sizes moved to their own section (Strategy paper trade amount, Portfolio cap, Alpaca paper order size)
- Order execution (order type, slippage, short selling, sizing mode) is its own section
- Alpaca credentials moved to full-width at the bottom

**Prompt Overrides section rewritten:**
- Per-symbol textareas now show meaningful placeholder examples matching the style of the actual backend prompts (EIA inventory signals for USO, BTC ETF inflow tracking for IBIT, etc.)
- Custom symbols (not in the built-in set) get an amber "no built-in context — fill this in" badge — their override is the model's only guidance
- Default symbols get a gray "supplements built-in guidance" badge
- Header explains exactly where the text is injected: appended as `Additional admin guidance for {symbol}:` in the stage-2 specialist prompt

**Bug fixes:**
- `PromptOverridesSection` was imported but never rendered; added to the symbols section under Advanced Mode
- `SystemSection` received five unused props (`isDirty`, `isSaving`, `status`, `handleSaveAndExit`, `save`) that were removed from both the type definition and call site

**Files changed:** `frontend/src/app/admin/page.tsx`, `frontend/src/components/admin/sections/BrokerageSection.tsx`, `frontend/src/components/admin/sections/OverviewSection.tsx`, `frontend/src/components/admin/sections/PromptOverridesSection.tsx`, `frontend/src/components/admin/sections/SystemSection.tsx`

---

### Fixed vs. vol-normalized order sizing

A new **Order sizing mode** toggle in Admin › Execution & Brokerage lets you choose how trade sizes are calculated:

- **Scale by vol & conviction** (default) — each trade is sized by the vol-normalization formula: `(1% × base) / ATR_14d_pct`, then scaled ×0.25–×5 by conviction. Applies to both paper simulation trades and live Alpaca orders.
- **Fixed amount** — every trade uses exactly the configured baseline dollar amount. Vol-scaling and conviction scaling are both skipped. Applies to paper and live equally.

Previously, live Alpaca orders always used the raw configured amount regardless of this setting, meaning vol-scaling never actually reached live orders. This is now corrected — when scaling is enabled, the vol-sized `paper_trade.amount` flows through to Alpaca notional unchanged.

**Files changed:** `backend/database/models.py`, `backend/database/migrate.py`, `backend/services/app_config.py`, `backend/services/paper_trading.py`, `backend/services/alpaca_broker.py`, `frontend/src/lib/utils/config-normalizer.ts`, `frontend/src/components/admin/sections/BrokerageSection.tsx`

**DB migration:** `alpaca_fixed_order_size` column added to `app_config` automatically on next backend restart (default `false` = scaling enabled).

### Domain cookie injection for paywalled sites

A new `backend/domain_cookies.json` file (never committed — added to `.gitignore`) lets you inject browser session cookies so Trafilatura can extract full article text from sites you have a personal subscription to (e.g. New York Times).

- Drop a Cookie-Editor JSON export directly into `backend/domain_cookies.json` — no conversion needed
- Also supports a manual dict format keyed by domain for multi-site use
- Cookies are matched by hostname suffix and injected into both the initial `requests` fetch and the Playwright fallback render
- File is re-read on every ingestion cycle so updates take effect without restarting the server
- Full setup instructions added to README under **Domain Cookies (Paywalled Sites)**

**Files changed:** `backend/services/data_ingestion/worker.py`, `.gitignore`, `README.md`

---

## Live Alpaca Position Reconciliation and Orphaned Order Handling

Tighter integration between Alpaca's live positions and the application's internal trade tracking.

**Pre-existing position baseline:**

- When the app opens a live Alpaca position for a symbol the user already held manually, it records the pre-existing size at entry
- Close operations only unwind the app-managed portion above that manual baseline, leaving the original holdings untouched
- Prevents the app from accidentally closing positions the user entered themselves outside the trading bot

**Tracked-symbol-only opens:**

- New Alpaca opens are blocked for symbols not in the user's configured `tracked_symbols` or `custom_symbols` list
- Prevents the app from entering positions in symbols it has no analytical coverage for, even if the trade logic would otherwise create a recommendation

**Orphaned order reconciliation:**

- New reconciliation logic (`poll_unfilled_orders`) detects Alpaca orders in the database with `pending` or `open` status that may have been filled while the app was offline
- Polls Alpaca's order history for each unfilled database record and updates the local status when a match is found
- Provides an API endpoint (`/alpaca/unfilled-orders`) to list orphaned orders and an acknowledge endpoint to manually resolve orders that cannot be matched
- The reconciliation runs automatically during status polling so the order log stays accurate across restarts

**Files changed:** `backend/services/alpaca_broker.py`, `backend/routers/alpaca.py`

**DB cleanup:**

- Removed the PostgreSQL migration tool (`alembic`) that was included during early development — migrations are handled entirely by `backend/database/migrate.py` (SQLite-only project)

**Files changed:** project root (removed alembic configuration and migration files)

---

## Telegram Bot — getUpdates Long-Polling and /snapshot Command

### Telegram rewrite with getUpdates polling

The Telegram bot was rewritten from webhook-based delivery to long-polling via Telegram's `getUpdates` API. This eliminates the need for a public HTTPS URL or webhook registration — the bot works behind NAT, VPNs, or local-only setups.

- Each poll cycle is a single `asyncio.to_thread` call with a 30-second max blocking timeout
- Cancels cleanly on backend shutdown — no dangling connections
- Every incoming message is checked against the stored `chat_id` from the keychain; messages from any other sender are silently dropped
- Commands supported: `/stop`, `/start`, `/status`, `/help`, `/snapshot`
- `/stop` saves the current Alpaca execution mode to `alpaca_pre_stop_mode` before setting it to `"off"`
- `/start` restores exactly what was running before the most recent `/stop`
- No other config fields can be mutated via Telegram — only the execution mode toggle

### /snapshot command

A new `/snapshot` command queues delivery of the most recent completed analysis run as a Telegram photo, bypassing the normal interval/change-detection gates. Users are informed they must restart the backend after enabling Telegram control for the `/snapshot` command to work.

### Remote stop/start banner

A persistent banner appears at the top of the trading page when the bot was remotely stopped or started via Telegram — it must be manually acknowledged to dismiss. This prevents confusion when the execution mode changes outside the Admin UI.

### Snapshot delivery fixes

Fixed snapshot rendering when `remote_snapshot_enabled` is off but a manual `/snapshot` command is issued — the snapshot now renders correctly regardless of the auto-snapshot toggle.

### Documentation split

- `README.md` was slimmed down to a quick-start orientation
- Detailed setup, configuration, and operational reference moved to a new `REFERENCE.md`
- Release notes updated to reflect the new Telegram polling architecture

**Files changed:** `backend/services/telegram_bot.py`, `backend/routers/config.py`, `backend/main.py`, `backend/services/remote_snapshot.py`, `frontend/src/app/trading/page.tsx`, `frontend/src/components/admin/modals/RemoteSnapshotSetupModal.tsx`, `README.md`, `REFERENCE.md` (new), `RELEASENOTES.md`

---

# Release Notes — May 2, 2026

## Quant-Quality Signal Improvements: Volatility-Normalized Sizing, Sentiment Decay, and Portfolio Cap

Three structural improvements to the signal generation and execution layer.

**Volatility-normalized position sizing:**

- Position size is now computed from a 1% daily volatility target instead of a flat notional amount
- Formula: `size = (1% × base_amount) / ATR_14d_pct`, then scaled by conviction (HIGH=1.5×, MEDIUM=1.0×, LOW=0.5×)
- High-volatility assets (BITO, crude) automatically receive smaller positions than low-volatility assets (SPY) for the same signal strength — size is inversely proportional to daily risk
- Example at $100 base: SPY ATR=0.8% MEDIUM→$125, HIGH→$187.50; BITO ATR=3.5% MEDIUM→$28.57
- Falls back to conviction-scaled base when ATR is unavailable (no price history pulled yet)
- Clamped to [0.25×, 5.0×] the configured base amount
- Flows through to Alpaca paper and live orders automatically — Alpaca reads `paper_trade.amount` directly as notional, so no broker-layer changes were needed
- Configurable in `logic_config.json` under `vol_sizing`: `enabled`, `target_daily_vol_pct`, `conviction_scalars`, `min_size_multiple`, `max_size_multiple`

**Sentiment half-life decay:**

- Directional scores are exponentially decayed based on hours elapsed since the previous analysis ran
- Formula: `decay_factor = max(min_factor, 0.5^(age_hours / half_life))` — a signal at its half-life age clears the entry threshold at half strength
- Per-symbol half-lives reflect how quickly each market absorbs news: SPY/QQQ=2h, USO=4h, BITO/IBIT=6h, default=3h
- Decay only gates threshold comparisons (entry and hysteresis keep-threshold) — raw scores for conviction level and basket-score weighting are unchanged
- Prevents stale news from sustaining hysteresis re-entries after the market has priced in the information
- Fresh analysis (`signal_age_hours=0`) produces decay_factor=1.0 — no impact on first-run signals
- Configurable in `logic_config.json` under `signal_decay`: `enabled`, `default_half_life_hours`, `symbol_half_lives`, `min_decay_factor`

**Portfolio cap:**

- A new **Portfolio Cap ($)** field in Admin › Trading Logic limits total open notional exposure across all symbols simultaneously
- When the cap is reached, new trade opens are skipped until an existing position closes and frees capacity
- If a single computed trade is larger than the remaining room it is scaled down to fit rather than skipped entirely — the cap controls cumulative exposure, not individual trade size
- Example: $5,000 account capped at $1,000 — after two MEDIUM-conviction SPY trades (~$250 each) only ~$500 is left; a third trade in a third symbol is sized down to $500 if needed, or skipped if there is no room at all
- Tracked via a running in-memory counter within each analysis loop (open positions opened earlier in the same run count against the cap immediately, even before the DB is committed)
- Configurable in `logic_config.json` under `vol_sizing.portfolio_cap_usd`; also overridable per-account from Admin › Trading Logic (`vol_sizing_portfolio_cap_usd`)

**Sentiment scoring and Stage 1 coverage improvements:**

- Strengthened Stage 1 keyword matching by scanning `title`, `summary`, `content`, and article `keywords`, reducing false negative coverage for custom tickers.
- Recalibrated per-symbol policy scoring so values now reflect both event-type base strength and matched source/support, instead of collapsing many symbols to the same generic geopolitical bucket.
- Added exposure-quality confidence adjustment for DIRECT/INDIRECT/BROAD/UNRELATED coverage, making confidence scores more meaningful by symbol.
- Relaxed the no-match fallback path so symbols with zero symbol-specific keyword hits can still reason over the broader filtered batch instead of immediately returning a flat neutral fallback.
- These changes improve custom-symbol coverage for names such as APLD, ORCL, SNOW, PEP, and WMT and make sentiment outputs less uniform.

---

# Release Notes — May 1, 2026

## Dashboard Deconstruction and Trading Enforcement Hardening

This release broke the monolithic `frontend/src/app/page.tsx` dashboard into smaller shared modules, tightened live/paper trading enforcement on the backend, and then shipped a quick follow-up fix for the UI regressions that surfaced after the refactor.

**Dashboard deconstruction:**

- Split the oversized dashboard page into focused reusable components including `SignalHero`, `AnalysisStatusCard`, `ArticleCard`, `PullHistoryCard`, `ModelComparePanel`, `DebugPanel`, `TradeExecutionModal`, `TradeCard`, and `ActualTradeComparisonCard`
- Moved shared dashboard contracts into `frontend/src/lib/types/analysis.ts` so component props and analysis payload shapes are centralized instead of being redeclared inline in `page.tsx`
- Moved shared constants into `frontend/src/lib/constants/analysis.ts`, including stage labels, local-storage keys, signal rules, and execution/underlying symbol maps
- Moved formatting, timing, and comparison helpers into `frontend/src/lib/utils/*` so the page now composes behavior instead of embedding hundreds of lines of local helper code
- Added a lightweight shared `GlassCard` wrapper so the extracted cards keep the same visual treatment without duplicating shell markup

**Backend trading enforcement:**

- Added `paper_trading_validator.py` to centralize paper-trading checks instead of scattering those guardrails through the execution path
- Hardened live Alpaca enforcement in `alpaca_broker.py` and paper-trading execution in `paper_trading.py` so trading-cap and validation behavior is handled more consistently
- Added dedicated backend coverage with `test_alpaca_broker_guards.py` and `test_paper_trading_enforcement.py` to lock down the new limit/validation paths

**Operational cleanup:**

- Added `.venv/` to `.gitignore` so local virtual environments stay out of the repo

---

# Release Notes — April 30, 2026

## Live Trading Guardrails and Bitcoin Proxy Update

This release tightened the real-money execution path, reduced same-day churn in the strategy layer, and switched future Bitcoin default coverage from `BITO` to `IBIT`.

**Alpaca per-symbol cap fix:**

- Live per-position caps now account for what Alpaca already holds in that symbol instead of only capping each new order in isolation
- Repeated same-direction confirmations no longer stack past the configured USD cap when live exposure is already full
- When the cap is already reached, the order is skipped and recorded in `alpaca_orders` with `status="skipped"` and an explicit reason
- Extended-hours opens now use the reduced remaining-capacity quantity rather than the original uncapped share count

**Pattern day trading protection:**

- Live execution now checks Alpaca account fields including equity, `daytrade_count`, and PDT flag status before sending orders
- For sub-$25k live accounts, the broker path can skip fresh opens and same-day closes when they would create PDT risk
- PDT-related skips are written to `alpaca_orders` so there is always an audit trail for why the order was not routed

**Trading page PDT visibility:**

- The `/trading` page now shows a live PDT status card with equity, `daytrade_count`, PDT flag state, and day-trading buying power
- The card surfaces a simple state badge (`clear`, `watch`, `warning`, `blocked`) so PDT risk is visible before the bot tries to trade

**Same-day churn filter:**

- Added a new trading-logic control: `min_same_day_exit_edge_pct`
- Default is `0.5%`, configurable from Admin, or inherited from `logic_config.json` when the field is left blank
- Same-day winners below that threshold are held instead of being closed on a flip, ticker/leverage change, or no-recommendation churn
- Loss-cutting is still allowed, so the filter only blocks tiny profitable churn, not defensive exits

**Bitcoin default proxy switched to `IBIT`:**

- Built-in default tracked symbols now use `IBIT` instead of `BITO`
- Legacy `BITO` inputs are normalized to `IBIT` for future runs so existing history remains readable while new Bitcoin trades use the new default
- Bitcoin validation, keyword maps, execution mapping, and UI defaults were updated to treat `IBIT` as the primary built-in symbol

---

# Release Notes — April 27, 2026

## Alpaca Live Brokerage Trading

Every paper trade open and close is now optionally mirrored to an Alpaca brokerage account in real time. Paper simulation always runs first and is always preserved regardless of what happens on the Alpaca side.

**Secrets and connection:**

- Alpaca API key + secret are stored in the OS keychain (Windows Credential Manager / macOS Keychain Access) through `keyring` — never in the repo or frontend bundle
- Paper mode (`paper-api.alpaca.markets`) and live mode (`api.alpaca.markets`) are stored alongside the credentials so the correct endpoint is always used
- A Test Connection button in Admin validates the stored keys and shows account equity before anything is enabled

**Order routing:**

- Open events route as a `buy` (long) or `sell` (direct short when `alpaca_allow_short_selling` is enabled)
- Close events route as `sell` (long close / inverse ETF close) or `buy` (cover for a direct short)
- Regular-hours closes submit the original **share quantity** rather than re-notionalising with the entry dollar amount, so orders match the live position size even when price has moved
- Extended-hours opens and closes automatically switch to `qty + limit_price` (entry price ± slippage) because Alpaca does not support notional/fractional orders outside regular hours
- Close orders are guarded: if no successful open is on record for the paper trade (e.g. the open was skipped because short selling was disabled, or a circuit breaker fired), the close is silently skipped to prevent unintended reverse exposure

**Window-expired closes dispatched to Alpaca:**

- `close_expired_positions()` now receives the pending dispatch list so conviction-window-expired closes are forwarded to Alpaca in the same post-commit dispatch call as all other lifecycle events; previously these exits committed in paper but were never sent live

**Circuit breakers (auto-disable live trading):**

- Max total open exposure exceeded
- Daily realized loss limit hit
- N consecutive losing trades (default 3)
- Any breach commits `alpaca_live_trading_enabled = false` to the database and logs the reason

**Guardrails (all configurable from Admin):**

- Per-position size cap (USD)
- Total open exposure cap (USD)
- Daily loss limit (USD)
- Max consecutive losses before circuit break
- Order type: `market` or `limit`
- Limit slippage percentage (applied to entry price for extended-hours limit orders)
- Allow direct short selling toggle

**Audit log:**

- Every order attempt — success, pending, or error — is written to a new `alpaca_orders` table with symbol, side, notional/qty, order type, status, fill price, trading mode, and Alpaca order ID
- Skipped and rejected opens are recorded with `status="error"` so there is always a traceable record of why a close was or was not sent

**Admin UI — Live Trading section:**

- Sits after Save Config, before Price History; always visible (not gated by Advanced Mode)
- API key / secret inputs (password-masked), mode selector (paper/live), Save Keys / Clear Keys buttons
- Configured/Not-set badge showing masked key prefix and active mode
- Test Connection with inline result showing account equity
- Account info cards: equity, buying power, cash, status (shown when keys are valid)
- Guardrail fields wired into the main Save Config flow
- Enable Live Trading button (disabled until keys are saved) with a "type LIVE to confirm" modal
- Disable button shown when live trading is active; one click, no confirmation needed

**Trading page updates:**

- Header renames to "Live Trading" and shows a pulsing red **LIVE** badge when `alpaca_live_trading_enabled` is true
- Alpaca Order Log table at page bottom whenever orders exist: symbol, side, notional/qty, order type, status, fill price, mode badge (LIVE/PAPER), submitted timestamp
- Alpaca status and orders are fetched in parallel with paper trading data on each page load

**DB migration:**

- 8 new columns added to `app_config` (all safe-default, non-breaking)
- New `alpaca_orders` table created with `CREATE TABLE IF NOT EXISTS` plus indexed columns
- Runs automatically on backend startup via `migrate.py`

---

# Release Notes — April 24, 2026

## Mac/Turbopack and Config Hardening

This update also folded in the Mac-side PR fixes and merged them with the local sentiment/trading work.

- **Frontend backend URL normalization** — all Next API proxy routes now resolve backend traffic through a shared helper that normalizes loopback URLs to `127.0.0.1:8000` instead of raw `localhost` fallbacks
- **Turbopack root pinned** — `frontend/next.config.js` now sets `turbopack.root` to the frontend directory so workspace detection is stable across machines, especially on macOS
- **Explicit dev scripts** — `frontend/package.json` now exposes both `dev:turbo` and `dev:webpack` so Webpack remains an easy fallback when Turbopack exposes local environment issues
- **Legacy config import made defensive** — `backend/services/app_config.py` now tolerates missing legacy columns during import and clamps/coerces persisted values before normalizing them into the live config row
- **Boolean parsing fixed** — persisted string booleans like `"false"` and `"0"` are now parsed correctly instead of becoming truthy through Python's default `bool("false")` behavior
- **Static Stage 1 trace clarified** — built-in symbols such as `SPY`, `QQQ`, `BITO`, and `USO` now show an explicit "static proxy map" explanation in the secret/debug view instead of the misleading "No Stage 1 prompt recorded" message

## Specialist Prompt Architecture Rewrite

The per-symbol specialist prompts were redesigned from the ground up to improve signal quality and reduce token cost.

- **Schema before news text** — the JSON schema and all field definitions now appear before the news text in every specialist prompt, so the model frames its reading with the full output contract first rather than discovering it after already processing the articles
- **Lean single-symbol header** — replaced the large basket-analysis context prompt with a focused 15-line header containing only the active symbol's price, specialist focus, and proxy-term context
- **Cross-symbol anchor removed** — all four symbol prices and basket-level signal rules were removed from the specialist path; each specialist now reasons about exactly one symbol with no cross-symbol contamination
- **proxy_context inline injection** — Stage 1 proxy-term context is now injected at the correct position within the header instead of prepended before system instructions
- **Exposure quality hint** — Stage 1 now computes a per-symbol exposure quality rating (DIRECT / INDIRECT / BROAD) from the keyword match ratio and injects it into the Stage 2 proxy context so specialists calibrate confidence on weakly-matched articles
- **~250 tokens saved per specialist call** — across four symbols, approximately 1,000 tokens removed per run with no reduction in extraction fidelity

## Signal Scoring Calibration

Several compounding biases were driving systematic SHORT signals on routine unconfirmed news. All three root causes are now corrected.

- **`unconfirmed_bluster_penalty`** lowered from 0.35 → 0.15: routine RSS articles (which are always technically "unconfirmed") no longer receive a structural negative penalty that pushed balanced articles toward the SHORT bluster path
- **`unconfirmed_policy_multiplier`** raised from 0.48 → 0.65: geopolitical unconfirmed news now scores 0.38 (near the LONG threshold); monetary policy unconfirmed now scores 0.53 (above the threshold) so partial-confirmation Fed commentary can produce HOLD/LONG instead of auto-SHORT
- **`bluster_short_threshold`** tightened from -0.35 → -0.60: requires substantially stronger bluster signal before an auto-SHORT triggers without policy backing
- **SHORT directional score** changed from `max(abs(bluster), policy)` to a weighted blend (40% bluster magnitude, 60% policy score): prevents pure rhetoric with zero policy evidence from producing a full-magnitude SHORT
- **`trade_policy` event type** added with base score 0.72: tariffs, trade war escalation, and import/export restrictions now have a dedicated bucket instead of splitting between `geopolitical` (0.58, too low) and `fiscal` (semantically wrong)

## Prompt Quality Improvements

- **Bluster phrase examples corrected** — examples now use genuinely rhetorical language ("promises to obliterate", "will change everything", "vows to completely destroy"); official hedge language from policy-makers ("warns that", "signals", "suggests") is explicitly excluded from bluster classification
- **Substance phrase examples expanded** — added "announced policy", "released data showing", "officially imposed"; clarified that press conference statements of official policy commitment count as substance
- **Neutral direction calibration** — `direction` field definition now instructs specialists to default to "neutral" unless the causal chain from headline to symbol price is explicit and direct; reduces spurious bearish classifications on loosely-related news
- **Red team balance** — red team is now explicitly instructed to challenge SHORT signals as vigorously as LONG signals, arguing why a bearish thesis may be priced in, the timeline uncertain, or the symbol hedged
- **Red team evidence thresholds visible** — minimum evidence counts required to override the blue team signal are now stated in the prompt so the model does not waste reasoning on overrides Python will silently discard (≥2 items for HOLD override; ≥3 for direction flip)
- **`event_type` disambiguation** — specialist schema now includes explicit classification guidance distinguishing `trade_policy` (tariffs, sanctions tied to trade) from `geopolitical` (military action, territorial conflict) and `monetary_policy` (central bank decisions)

## Prompt Schema Simplification

Fields that Python can compute more reliably than the LLM are now computed in Python.

- **`holding_period_hours` removed** from LLM schema; Python derives from `trading_type` via lookup table (SCALP→2h, VOLATILE_EVENT→3h, SWING→12h, POSITION→72h)
- **`transmission_path` removed** — duplicate of `mechanism` inside `symbol_relevance`; Python now reads `mechanism` directly
- **`urgency` and `conviction` removed** from schema; Python derives both from `trading_type` and `exposure_type` (e.g. POSITION+DIRECT → HIGH conviction; BROAD exposure caps at MEDIUM regardless of trade type)
- **`source_count` injected as Python fact** — actual article count is now stated in the prompt header; LLM copies the number instead of guessing (previously almost always defaulted to 2)
- **Redundant rule blocks removed** — symbol differentiation rules (150 tokens, fully covered by `exposure_type` definition) and phrase extraction rules (60 tokens, covered by field definitions) replaced with two concise bullets
- **Dead code removed** — `SYMBOL_SPECIALIST_APPENDIX` deleted; `STAGE1_EXTRACTION_PROMPT` marked as legacy (main pipeline uses keyword matching, not LLM classification)
- **`COMBINED_ANALYSIS_PROMPT` hardcoding fixed** — removed hardcoded USO/BITO/QQQ/SPY `symbol_impacts` block; fallback path no longer emits fake symbol analysis for custom-symbol runs

## Paper Trading Logic

- **Trail on window expiry** (`trail_on_window_expiry`, default true) — when a conviction holding window expires, the position now transitions to trailing stop mode instead of closing flat; lets profitable positions run while still protecting gains
- **Re-entry cooldown** (`reentry_cooldown_minutes`, default 120) — blocks same-direction re-entry in the same symbol within the configured window after a close; prevents same-direction churn on choppy signals
- **Entry threshold raised** (0.30 → 0.42) — higher minimum directional score required before a new paper trade opens; filters out the lowest-conviction noise signals

## Admin UI

- **Trading Logic section repositioned** — now sits between Model Orchestration and Symbols so trading behavior controls are grouped with the pipeline config they affect
- **Extended-hours toggle moved into Trading Logic** — the "Allow pre-market and after-hours paper trading" checkbox now lives alongside the other simulation controls with session liquidity guidance
- **Trail on window expiry toggle** added — checkbox with explanation of trailing stop mechanics and when to disable
- **Re-entry cooldown field** added — configurable minutes with fallback to `logic_config.json` default

---

# Release Notes — April 23, 2026

## Remote Snapshot Delivery and Secure Telegram Secrets

This release added outbound remote run snapshots plus cross-platform secure Telegram secret storage.

- Added **remote snapshot delivery** that renders a compact PNG after qualifying runs with latest recommendations, current P&L, timestamp, model label, and request ID
- Added **material-change gating** so snapshots only send when recommendations changed, net P&L moved enough, or the heartbeat window elapsed
- Added **Telegram photo delivery** as the primary remote destination, with signed-link and email plumbing available behind the same snapshot pipeline
- Added Admin controls for remote snapshot settings: enable/disable, delivery mode, max recommendations, P&L resend threshold, heartbeat, and whether to include recent closed trades
- Added a **Send Snapshot Now** button in Admin that immediately queues delivery of the most recent completed analysis run, bypassing the normal interval/change-detection gates — useful for testing credentials or manually pushing an update
- Added a Telegram setup modal in Admin with step-by-step instructions for bot creation and chat ID discovery
- Added secure **UI-managed Telegram secrets** using the OS keychain through `keyring`
  - Windows stores them in Credential Manager
  - macOS stores them in Keychain Access
- Raw bot token and chat ID are never stored in repo config, never returned to the UI after save, and only masked status is shown back in Admin
- Added a new backend admin secret-management API and a frontend proxy route so secrets stay backend-only

## Paper Trading Logic Overhaul

Several correctness issues and three structural improvements to the paper trading simulation.

**Bug fixes:**

- Fixed a silent `NameError` that prevented any paper trade from ever opening after a data reset — `config` was referenced inside a `try` block before it was defined, so the exception was swallowed and no trades were created
- Conviction window expiry now closes positions regardless of market hours — the `close_expired_positions` call was previously gated behind the market-closed check, so overnight expirations were silently skipped until the next open-market run

**New trading logic:**

- **ATR-scaled leverage caps** — leverage is now capped by the 14-day ATR % before the position is opened. When ATR % exceeds `high_vol_atr_pct` (3.0 %) leverage is capped at 1x; above `medium_vol_atr_pct` (1.5 %) it is capped at 2x. Thresholds are configurable in `logic_config.json`.
- **Dynamic per-symbol materiality gate** — the gate that blocks thesis flips on trivial re-runs now uses a rolling article baseline (mean ± 1 stddev over the last 20 runs per symbol) instead of a fixed post-count threshold. The dynamic threshold activates once 5 runs of history exist and falls back to the fixed threshold until then.
- **Trailing stop on HOLD** — instead of force-closing a position when a HOLD signal fires, the system now sets a trailing stop at `best_price_seen × (1 ± stop_loss_pct × tighten_factor)`. The stop tightens every run while the position is in HOLD mode and closes the position only if price crosses the stop level. Thesis re-confirmation clears the trailing stop.
- **Conviction window reset on re-confirmation** — when a re-run confirms the same direction, the holding window resets to a full window (or max window if a cap applies). Same or upgraded trade type resets fully; downgraded type shrinks proportionally. SCALP / SWING / POSITION / VOLATILE_EVENT are all handled by a `trading_type` rank rather than conviction level.

**Price history:**

- Price history is now automatically pulled in the background when a new custom symbol is added via Admin. History is retained even if the symbol is later removed, so re-adding it skips a fresh pull.

**History display:**

- Analysis history labels now show both `extraction_model` and `reasoning_model` when they differ (e.g. `qwen3:8b / qwen3:14b`). Previously only a single model name was shown even in two-stage pipeline runs.

---

## Producer/Consumer Ingestion Refactor

Today's backend update moved article ingestion out of the hot `/analyze` request path and into a DB-backed producer/consumer flow so analysis runs no longer block on live RSS fetches or full-page scraping.

- Added a new `scraped_articles` queue table to persist discovered RSS items, cleaned full article text, timestamps, and `processed` / `fast_lane_triggered` state
- Added a background ingestion worker that polls RSS feeds, runs a lightweight Stage 0 relevance pass, extracts cleaned article text, and saves unique URLs for later analysis
- Refactored the batch `/analyze` path to consume pending `processed = false` articles from the database instead of scraping the web inline
- Added a Fast Lane path for urgent macro headlines so high-impact summaries can trigger an off-cycle analysis run instead of waiting for the normal 15-minute batch
- Added analysis lease fields in `app_config` so scheduled runs and urgent runs do not process the same queued articles in parallel

## Startup and SQLite Stability

This release also hardened first boot and overlapping background work, which had started to show up as SQLite lock errors during startup.

- Added a startup grace window before the ingestion scheduler begins heavy work
- The ingestion worker now defers while an analysis lease is active, reducing writer contention during app boot and urgent reruns
- SQLite write windows were shortened and the engine was configured with a busy timeout to make lock recovery more forgiving under local single-user load

---

# Release Notes — April 22, 2026

## Paper Trading Simulation

Every analysis run now automatically simulates a $100 paper trade per signal during extended market hours (4:00am-8:00pm ET, Monday-Friday). The simulation mirrors what a real trader following every signal would actually do:

- **HOLD signal** — leave any open position running, don't open if flat. No action taken.
- **Same direction, same leverage, same execution ticker** — position is unchanged. No action taken.
- **Any change** (ticker flip, leverage increase/decrease, direction reversal) — close the existing position at the current price, open a new $100 position in the new direction.

Extended hours are used because that is when pre-market and after-hours paper or real trades can happen.

**Position tracking is per underlying symbol** — one open position per symbol (USO, BITO, QQQ, SPY, or any custom symbol) at a time. Entry price and shares are stored when a position is opened. Exit price and realized P&L are computed when it closes.

**New `/trading` page** — accessible from the nav (between History and Compare) shows the full paper trading picture:

- Market session badge showing current status (Open / Pre-Market / After-Hours / Closed)
- 8 summary stat cards: Net P&L, Realized P&L, Open P&L, Win Rate, Avg Win, Avg Loss, Total Deployed, Total Trades
- Equity curve — cumulative realized P&L across all closed trades, color-coded green above zero / red below
- Open positions table with live unrealized P&L fetched from yfinance at page load
- Closed trades history with entry->exit prices, realized P&L, and market session each trade was opened in
- Reset button (admin) to clear all paper trading history

**New `paper_trades` database table** — completely independent of all analysis tables; never touched by reset-data operations. Stores one row per opened position with entry/exit prices, timestamps, session, shares, and P&L.

**DB upgrade note:** Restart the backend — `migrate.py` creates the `paper_trades` table automatically.

---

# Release Notes — April 21, 2026

## Initial Release

First working end-to-end build of the sentiment trading pipeline.

## Local-Only Security Model

- Backend binds to `127.0.0.1` by default — not publicly exposed
- Optional `ADMIN_API_TOKEN` environment variable gates sensitive admin routes with a shared-secret header
- Generated build artifacts, caches, and local databases excluded from git tracking

## Core Analysis Pipeline

- FastAPI backend with SSE streaming for live progress and article events
- RSS ingestion across 7 sources with per-feed fair distribution so no single feed dominates the article pool
- Keyword relevance filtering before LLM analysis — generic noise less likely to dominate the run
- Symbol-specific specialist prompts: each tracked symbol gets its own narrowed news context and validation block rather than a shared aggregated blob
- FRED validation for BITO (M2SL / M2REAL), QQQ (DFII10), and SPY (HY and IG credit spreads); EIA validation for USO (crude stocks, refinery utilization)
- QQQ and SPY added to default coverage alongside USO and BITO

## Frontend Baseline

- Next.js 16.2.4 / React 19 dashboard
- Live market price polling panel
- Auto-run countdown and manual trigger
- Expandable article cards in the live feed with source, title, and content preview
- Signal presentation with action, symbol, leverage, and confidence

## Advanced Mode

- Toggle reveals debug panels for: RSS articles fed to the model, compiled news context, FRED/EIA validation blocks, technical indicator context per symbol, and exact final per-symbol prompts
- Frozen snapshot comparison lab available in Advanced Mode — replay a saved dataset against a different Ollama-served model without re-downloading articles or validation data