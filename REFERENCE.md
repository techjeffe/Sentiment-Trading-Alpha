# Reference Documentation

> Advanced configuration, API reference, position sizing math, validation sources, and other reference material for Sentiment Trading Alpha.

---

## Project Structure

```text
qwen-3.5-9b-getrich/
|- backend/
|  |- main.py
|  |- test_stage1.py
|  |- routers/
|  |  |- analysis.py
|  |  |- config.py
|  |  `- alpaca.py
|  |- schemas/
|  |  `- analysis.py
|  |- database/
|  |  |- engine.py
|  |  |- models.py
|  |  `- migrate.py
|  `- services/
|     |- data_ingestion/
|     |  |- parser.py
|     |  |- scraper.py
|     |  |- market_validation.py
|     |  `- yfinance_client.py
|     |- sentiment/
|     |  |- engine.py
|     |  `- prompts.py
|     |- alpaca_broker.py
|     |- secret_store.py
|     |- runtime_health.py
|     |- trading_instruments.py
|     `- paper_trading.py
|- frontend/
|  |- src/app/page.tsx
|  |- src/app/admin/page.tsx
|  |- src/app/health/
|  |- src/app/trading/
|  |- src/lib/
|  `- src/app/api/
|     |- paper-trading/route.ts
|     |- admin/price-history/
|     `- alpaca/
|- RELEASENOTES.md
`- README.md
```

---

## Frontend Stack

- Next.js 16.2.4
- React 19.2

---

## API Reference

### `POST /api/v1/analyze/stream`

SSE pipeline. Events: `log`, `article`, `result`, `error`.

Example request:
```json
{ "symbols": ["USO", "IBIT", "QQQ", "SPY"], "max_posts": 50, "lookback_days": 14 }
```

`result` payloads include:
- `market_validation` — per-symbol FRED/EIA structured metrics
- `model_inputs.news_context` — compiled text sent to the model
- `model_inputs.validation_context` — validation summary injected into the prompt
- `model_inputs.per_symbol_prompts` — exact final prompt preview for each analyst
- `model_inputs.web_context_by_symbol` — saved per-symbol web research summary
- `model_inputs.web_items_by_symbol` — structured web research items shown in Advanced Mode

### `GET /api/v1/analysis-snapshots`

Returns recent saved frozen analysis snapshots for Advanced Mode replay.

### `POST /api/v1/analysis-snapshots/{request_id}/rerun`

Replays a frozen snapshot. Supports single-model and two-stage pipelines.

Single model:
```json
{ "model_name": "qwen3.5:14b" }
```

Two-stage:
```json
{ "extraction_model": "llama3.2:3b", "reasoning_model": "qwen3:9b" }
```

### `GET /api/v1/ollama/status`

Returns whether Ollama is reachable and the active served model.

### `GET /api/v1/prices`

```json
{
  "USO":  { "price": 128.25, "change": 7.80, "change_pct": 6.47, "day_low": 121.03, "day_high": 128.88 },
  "QQQ":  { "price": 501.12, "change": 2.31, "change_pct": 0.46, "day_low": 497.20, "day_high": 502.05 },
  "SPY":  { "price": 612.40, "change": 1.44, "change_pct": 0.24, "day_low": 609.98, "day_high": 613.11 }
}
```

### `GET /health` and `GET /metrics`

`/health` returns model reachability, active model name, uptime, request latency, latest data-pull state, and recent analysis timing.

`/metrics` is a lightweight internal summary payload.

---

## Domain Cookies (Paywalled Sites)

Some RSS sources (e.g. New York Times) are paywalled — Trafilatura extracts only the short RSS blurb instead of the full article. If you have a personal subscription you can inject your login cookies so the scraper fetches the full text.

**This is for personal use against your own subscription only.**

### Step 1 — Export cookies from your browser

Install a cookie export extension such as [Cookie-Editor](https://cookie-editor.com/) (Chrome/Firefox/Safari). Navigate to the site while logged in, open the extension, and export as **JSON**. Save the file.

### Step 2 — Drop the file in the backend directory

```bash
# macOS / Linux
cp ~/Downloads/cookies.json backend/domain_cookies.json

# Windows (PowerShell)
Copy-Item "$env:USERPROFILE\Downloads\cookies.json" backend\domain_cookies.json
```

The file is read fresh on every ingestion cycle — no restart needed. The filename is in `.gitignore` so it will never be committed.

### Supported formats

**Array format** (Cookie-Editor / EditThisCookie export — paste as-is):
```json
[
  { "domain": ".nytimes.com", "name": "NYT-S", "value": "…", "path": "/", "secure": true },
  { "domain": ".nytimes.com", "name": "nyt-a",  "value": "…", "path": "/" }
]
```

**Dict format** (manual / multi-site):
```json
{
  "nytimes.com": [
    { "name": "NYT-S", "value": "…" },
    { "name": "nyt-a",  "value": "…" }
  ],
  "wsj.com": [
    { "name": "wsjregion", "value": "…" }
  ]
}
```

Cookies are matched by hostname suffix, so `.nytimes.com` and `nytimes.com` both match `www.nytimes.com`. They are injected into both the initial `requests` fetch and the Playwright fallback render.

### Checking it works

After the next ingestion cycle, query the database:

```bash
sqlite3 trading_system.db \
  "SELECT url, length(full_content) FROM scraped_articles WHERE url LIKE '%nytimes%' ORDER BY id DESC LIMIT 5;"
```

A `length(full_content)` above ~1000 means the full article was extracted. If it stays at a few hundred, the session may have expired — re-export and replace the file.

### Session expiry

NYT sessions typically last 30 days. When articles start showing short lengths again, re-export cookies from your browser and replace `backend/domain_cookies.json`.

---

## Schema Migration Reference

The backend runs `migrate.py` automatically on every startup. This table is for reference only — no manual SQL is required.

| Column / Table | Table | Default | Purpose |
|---|---|---|---|
| `price_history` | table | — | OHLCV price history (never cleared by reset-data) |
| `extraction_model` | `app_config` | `''` | Two-stage pipeline |
| `reasoning_model` | `app_config` | `''` | Two-stage pipeline |
| `risk_profile` | `app_config` | `'aggressive'` | Risk profile selector |
| `rss_article_detail_mode` | `app_config` | `'normal'` | Depth mode selector |
| `rss_article_limits` | `app_config` | `{"light":5,"normal":15,"detailed":25}` | Per-depth article caps |
| `snapshot_retention_limit` | `app_config` | `12` | Snapshot pruning |
| `display_timezone` | `app_config` | `''` | Timezone display |
| `custom_symbols` | `app_config` | `[]` | Custom symbol support |
| `underlying_symbol` | `trades` | `NULL` | Execution ticker mapping |
| `conviction_level` | `trades` | `'MEDIUM'` | Signal conviction |
| `holding_period_hours` | `trades` | `4` | Holding horizon |
| `trading_type` | `trades` | `'SWING'` | Trade duration type |
| `trade_closes` | table | — | Realized P&L recording |
| `paper_trades` | table | — | Auto-simulated paper trades |
| `scraped_articles` | table | — | DB-backed article queue |
| `sec_filings` | table | — | SEC EDGAR filings for tracked symbols |
| `symbol_edgar_ciks` | `app_config` | `{}` | CIK cache for EDGAR integration |
| `analysis_lock_request_id` | `app_config` | `''` | Active analysis lease owner |
| `analysis_lock_acquired_at` | `app_config` | `NULL` | Analysis lease start time |
| `analysis_lock_expires_at` | `app_config` | `NULL` | Analysis lease expiry time |
| `remote_snapshot_enabled` | `app_config` | `false` | Enable outbound remote PNG delivery |
| `telegram_remote_control_enabled` | `app_config` | `false` | Enable Telegram remote control |
| `remote_snapshot_mode` | `app_config` | `'telegram'` | Delivery backend |
| `remote_snapshot_min_pnl_change_usd` | `app_config` | `5.0` | Re-send threshold |
| `remote_snapshot_heartbeat_minutes` | `app_config` | `360` | Re-send heartbeat |
| `remote_snapshot_include_closed_trades` | `app_config` | `false` | Include closed positions in image |
| `remote_snapshot_max_recommendations` | `app_config` | `4` | Max recommendations rendered in image |
| `last_remote_snapshot_sent_at` | `app_config` | `NULL` | Last successful outbound snapshot time |
| `last_remote_snapshot_request_id` | `app_config` | `NULL` | Request id tied to last outbound snapshot |
| `trailing_stop_price` | `paper_trades` | `NULL` | Trailing stop level |
| `best_price_seen` | `paper_trades` | `NULL` | High/low-water mark for trailing stop |
| `trail_on_window_expiry` | `app_config` | `true` | Transition to trailing stop on window expiry |
| `reentry_cooldown_minutes` | `app_config` | `NULL` | Block same-direction re-entry for N minutes |
| `opening_range` | `logic_config.json` | enabled; wait 15m; range 15m; break 0.2% | ORB guard: skip new exposure during opening wait, block entries against the opening break |
| `min_same_day_exit_edge_pct` | `app_config` | `NULL` | Minimum profit edge before closing same-day winner |
| `alpaca_live_trading_enabled` | `app_config` | `false` | Master kill switch for real-money orders |
| `alpaca_allow_short_selling` | `app_config` | `false` | Allow direct short sells |
| `alpaca_max_position_usd` | `app_config` | `NULL` | Per-trade notional cap |
| `alpaca_max_total_exposure_usd` | `app_config` | `NULL` | Total open exposure circuit breaker |
| `alpaca_order_type` | `app_config` | `'market'` | `market` or `limit` |
| `alpaca_limit_slippage_pct` | `app_config` | `0.002` | Slippage added to limit price |
| `alpaca_daily_loss_limit_usd` | `app_config` | `NULL` | Daily realized loss circuit breaker |
| `alpaca_max_consecutive_losses` | `app_config` | `3` | Consecutive losses before auto-disable |
| `alpaca_high_conviction_override_enabled` | `app_config` | `false` | High conviction override for PDT |
| `edgar_filings_enabled` | `app_config` | `NULL` | EDGAR filings integration toggle |
| `edgar_filings_poll_interval_minutes` | `app_config` | `NULL` | EDGAR poll interval override |
| `edgar_filings_tracked_form_types` | `app_config` | `NULL` | EDGAR form types override |
| `edgar_filings_material_8k_items` | `app_config` | `NULL` | EDGAR material 8-K items override |
| `alpaca_orders` | table | — | Full audit log of every Alpaca order attempt |

To run the migration manually:

```powershell
cd backend
python -m database.migrate
```

---

## Testing Stage 1 Extraction

Before committing to a model for Stage 1 entity extraction, run the smoke test:

```powershell
cd backend
python test_stage1.py
```

Or with a specific model:

```powershell
python test_stage1.py llama3.2:latest
```

The test covers both built-in symbols (USO/IBIT/QQQ/SPY, which use a static keyword map and require no LLM call) and custom symbols (NVDA/NOW, which call the LLM once to generate proxy keywords). It prints:

- Which keyword source was used per symbol: `(static)` or `(LLM-generated)`
- The generated keywords for each custom symbol
- How many articles each path caught
- Separate pass/fail for built-in catch rate, custom symbol coverage, and noise filtering

**What to look for:**

| Output | Meaning |
|---|---|
| `Stage 1 keyword filter: 10/13 articles matched` | Working correctly |
| `✓ PASS: Custom symbols (NVDA/NOW) caught at least 1 article` | LLM keyword generation is working |
| `✓ PASS: LLM generated keywords for custom symbols (>1 term each)` | Keywords were successfully generated and cached |
| `✓ PASS: Noise headlines correctly filtered out` | Sports / celebrity articles removed |
| `Stage 1: keyword generation failed for NVDA (...)` | LLM call failed — check Ollama is running and the model is loaded |

### If Stage 1 fails

The pipeline degrades gracefully — it falls back to sending all articles to Stage 2 instead of only the relevant ones. Analysis still completes and produces signals, but Stage 2 receives more noise. If keyword generation fails for a custom symbol, the ticker name itself is used as the fallback keyword.

### Model guidance

- Stage 1 calls the LLM only to answer "what keywords appear in [SYMBOL] news?" — a short factual question. Even llama3.2 (3B) handles this reliably.
- The article classification step (reading all headlines) has been removed — Stage 1 is now fast regardless of article count.
- Keyword generation results are cached for the server session. Restarting the server will re-generate keywords for custom symbols on the next run.
- The test only takes a few seconds since it skips RSS ingestion, price fetching, and validation entirely.

---

## Position Sizing Detail

The `paper_trade_amount` setting (default $100) is a **base amount, not a fixed trade size**. Each trade is sized by volatility: calmer assets get a bigger slice, wilder assets get a smaller one. Floor is $25, ceiling is $500.

**Formula:** `trade size = (1% × base_amount) / ATR_14d_pct`, then scaled by conviction level.

Typical sizes at $100 base:

| Symbol | Typical ATR | LOW conviction | MEDIUM conviction | HIGH conviction |
|---|---|---|---|---|
| SPY | ~0.8% | $62.50 | $125.00 | $187.50 |
| QQQ | ~1.2% | $41.67 | $83.33 | $125.00 |
| USO | ~2.0% | $25.00 | $50.00 | $75.00 |
| BITO/IBIT | ~3.5% | $25.00 | $28.57 | $42.86 |

**Example for a $1,000 account:**

- A single HIGH-conviction SPY trade uses ~$187 — about 19% of the account
- All four symbols firing at HIGH conviction simultaneously would deploy ~$430 total
- All four at MEDIUM conviction would deploy ~$287 total
- The $25 floor and $500 ceiling are hard clamps regardless of ATR

If price history has not been pulled for a symbol, ATR is unknown and the trade falls back to conviction-scaled base: LOW=$50, MEDIUM=$100, HIGH=$150.

Set a **Portfolio Cap ($)** in Admin › Trading Logic to hard-limit total open exposure. When the cap is reached, new trades are skipped. If a single computed trade size would exceed the remaining room, it is scaled down to fit rather than skipped entirely.

---

## Validation Sources

- `USO` — EIA weekly petroleum pages (refinery utilization, commercial crude stocks, gasoline stocks, distillate stocks)
- `IBIT` — FRED `M2SL` and `M2REAL`
- `QQQ` — FRED `DFII10` (10-year TIPS real yield)
- `SPY` — FRED `BAMLH0A0HYM2` and `BAMLC0A0CM` (credit spreads)

These signals are injected into the symbol specialist prompt as per-symbol context, not one shared generic block. Only built-in symbols have the richer FRED/EIA bundles; custom symbols do not.

---

## Technical Indicators Detail

| Indicator | Parameters | Notes |
|---|---|---|
| RSI | 14-period | Momentum oscillator |
| SMA | 50-day and 200-day | Golden Cross / Death Cross flagged automatically |
| MACD | 12/26/9 | Histogram included |
| Volume Profile | 20-day average | Reports above / at / below average |
| Bollinger Bands %B | 14-period | Price position within bands |
| ATR | 14-period | Volatility measure |
| OBV Trend | Last 5 sessions | Rising / Falling / Flat |

All computed locally from the `price_history` table using numpy. If fewer than 14 days of data are stored, the affected indicator is omitted rather than erroring.

---

## Feed Sources

- Most geopolitical and market headlines come from standard RSS feeds
- Truth Social coverage comes from the third-party RSS feed `https://trumpstruth.org/feed`
- Direct Playwright scraping of Truth Social is not the active production path
- 7 built-in RSS sources by default: Trump Truth Social, BBC World, Marketwatch, NPR World, Calculated Risk, Reuters Business, and New York Times Business

---

## Advanced Mode / Snapshot Comparison Lab

When Advanced Mode is enabled, the comparison lab lets you:

- Pick the current run or a recent frozen snapshot
- See the snapshot date, model, and article count in the picker
- Choose Stage 1 and Stage 2 models independently for the comparison run
- Use **Rerun original** to replay with the exact models the snapshot was first run with
- Compare signal direction and runtime between model configurations

Comparison results include per-symbol recommendation diffs, reasoning summaries for both baseline and comparison, and live feed entries for symbol-scoped web research when Light Web Research is enabled.

The Admin page controls how many frozen snapshots to retain. Older snapshots and their related trade history are pruned automatically after each new save once the configured limit is exceeded.

---

## Windows-Specific Notes

- `run.py` sets the Windows event loop policy before Uvicorn starts, which is required for Playwright browser subprocesses
- Defaults `UVICORN_RELOAD` to off — Uvicorn's reload mode switches back to `_WindowsSelectorEventLoop` and breaks Playwright
- Frontend API proxy routes normalize backend loopback traffic to `127.0.0.1:8000` instead of `localhost` to avoid environment-specific issues
- `frontend/next.config.js` pins `turbopack.root` to the frontend directory for consistent workspace resolution
- If `npm run dev:turbo` misbehaves: `npm run dev:webpack` is the supported fallback

Alternative dev server modes (both platforms):

```powershell
npm run dev:turbo    # Turbopack
npm run dev:webpack  # fallback
---

## SEC EDGAR Filings Integration

Polls SEC EDGAR for new filings from tracked operating companies and stores them for LLM-enhanced sentiment analysis.

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/edgar/filings` | GET | List stored filings (filter by symbol, processed) |
| `/api/v1/edgar/poll` | POST | Manually trigger EDGAR poll cycle |
| `/api/v1/edgar/process` | POST | Process unprocessed filings (fetch text, LLM summary) |
| `/api/v1/edgar/config` | GET | Get current EDGAR configuration |
| `/api/v1/edgar/config` | PUT | Update EDGAR configuration |

### Configuration

EDGAR settings are in `backend/config/logic_config.json` under `edgar_filings` block:

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable/disable EDGAR integration |
| `poll_interval_minutes` | `60` | Polling interval |
| `tracked_form_types` | `["10-K", "10-Q", "8-K"]` | SEC form types to poll |
| `material_8k_items` | `["2.02", "5.02", "7.01", "8.01"]` | Material 8-K item codes |
| `max_filing_chars_for_llm` | `40000` | Max chars sent to LLM |
| `lookback_days_on_first_poll` | `7` | Initial lookback period |

Override any setting via Admin UI (stored in `app_config` table).

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `EDGAR_USER_AGENT` | Yes | Compliant SEC User-Agent (e.g., `Sentiment Trading Alpha admin@example.com`) |

### Database

- `sec_filings` table: Stores filing metadata and processed text/summaries
- `app_config.symbol_edgar_ciks`: JSON cache of ticker→CIK mappings

### Background Polling

The `_edgar_polling_scheduler_loop()` runs as an async task in the FastAPI lifespan handler, checking for new filings at the configured interval.

### Sentiment Engine Integration

Recent filing summaries are automatically included in the symbol specialist prompt when analyzing tracked operating companies. The context is passed via `edgar_filing_context` parameter to `format_symbol_specialist_context_prompt()`.

### Testing

```powershell
# Test EDGAR client
cd backend
python -m services.data_ingestion.edgar_client

# Test EDGAR worker (poll + process)
python -m services.data_ingestion.edgar_worker
```
