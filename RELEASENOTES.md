# Release Notes — 2026-07-28

## 🔧 Discovery "Discover New" Button + Auto-Add Reliability

Follow-up hardening of the automatic ticker discovery feature after end-to-end testing.

### What Changed
- **"Discover New" button now populates the list in place** (`frontend/src/app/trade-list/page.tsx`). Previously it did `window.open('/api/v1/discover', …)`, dumping raw JSON in a new tab and never refreshing the trade list — auto-added opportunities only appeared on the next 2-minute poll. It now `fetch`es the endpoint and calls `fetchTradeList()` immediately, with a disabled "⏳ Discovering…" state and error-banner surfacing on failure.
- **Correct trailing-slash path.** The button (and any client) now calls `/api/v1/discover/?auto_add=true` directly instead of relying on FastAPI's 307 redirect from the slash-less form.
- **Unified `AggregatedSignal` type.** `backend/services/risk/pump_dump_detector.py` defined its own structurally-duplicated `AggregatedSignal`; it now imports the canonical one from `services.analysis.signal_aggregator`, so `aggregate_signals()`, `check_pnd_flags()`, and `default_score()` share a single type instead of relying on duck-typing.

### Testing
- Verified live: `discover_opportunities(min_score=0, auto_add=True)` processed 100 articles, extracted 151 tickers, fetched 3 SEC insider signals, scored and auto-added 20 opportunities; `trading_opportunities` watchlist count confirmed at 20.
- Confirmed `AggregatedSignal` is a single identity across all three modules (`A is B is C == True`).

### Known Behavior
- At the default `min_score=30`, only catalyst-backed (e.g. SEC insider) opportunities pass; news-only signals score ~10–14 and are intentionally filtered as social-only noise. When the SEC/OpenInsider feed times out, a discovery run can legitimately return zero opportunities. Surfacing that fetch failure in the UI is a recommended follow-up.

### Files Changed
- `frontend/src/app/trade-list/page.tsx` — in-place discovery refresh, trailing-slash fetch, discovering state
- `backend/services/risk/pump_dump_detector.py` — import canonical `AggregatedSignal`, remove duplicate definition

---

## 🎯 Major Fix: "Start Trading" Button Now Actually Works!

### What Changed
The "Start Trading" button in the Trade List page was broken — it updated the status but **didn't actually add symbols to the tracking list**. Now it works properly!

### What "Start Trading" Does Now:
When you click "Start Trading" on any symbol, it:
1. ✅ **Adds the symbol to tracked symbols** — The analysis pipeline will now include it
2. ✅ **Downloads price history** — Gets historical data for backtesting  
3. ✅ **Generates search keywords** — Creates terms for sentiment analysis
4. ✅ **Handles duplicates gracefully** — If the symbol is already being tracked, it merges instead of throwing an error

**Before:** Clicking "Start Trading" just changed the status in the list but didn't actually track the symbol.
**Now:** The symbol is immediately added to your tracked list and will be analyzed in the next run.

### Removed Features:
- **Discovery page removed** — The `/discovery` page was removed to simplify the app. All functionality is now available through the Trade List.

### Bug Fixes:
- **Fixed 500 error** when clicking "Start Trading" — Added proper error handling
- **Fixed duplicate trading entries** — Prevents multiple "trading" entries for the same symbol
- **Fixed navigation** — Removed broken Discovery link from the header

---

## 🎉 Major Feature: Automatic Ticker Discovery & Trade List Management

### SignalScope Integration Complete
Successfully integrated **SignalScope's automatic ticker discovery system** into Sentiment Trading Alpha. The system now automatically discovers trading opportunities from news articles and SEC filings without requiring users to manually specify ticker symbols.

---

## What's New

### 1. Automatic Ticker Discovery (Phase 1-4 Complete)

**Phase 1: Ticker Extraction Module** ✓
- Automatically extracts ticker symbols from unstructured news text
- Filters out 200+ blacklisted words (English words, ETFs, crypto, etc.)
- Handles cashtag format ($AAPL)
- **31 unit tests** - ALL PASSING
- Tested on 50+ real news articles: **221 tickers discovered automatically**

**Phase 2: Signal Sources** ✓
- **SEC Insider Client**: Fetches insider trading data from OpenInsider.com
  - Filters for C-suite/director purchases ≥ $50K
  - Successfully fetched 3-4 insider purchases in testing
- **Reddit Client**: Scans 17 subreddits for stock mentions (optional, requires API credentials)
- **Signal Aggregator**: Aggregates raw signals by ticker symbol
  - Calculates velocity, momentum, source diversity
  - Successfully aggregated 101 tickers from 175 signals

**Phase 3: Scoring & Risk Management** ✓
- **Pump & Dump Detector**: 11-flag rule-based detection system (no AI required)
  - Filters out scam stocks before recommendations
  - All tests passing
- **Advanced Scorer**: AI scoring system with Ollama integration
  - Heuristic fallback scoring
  - Social-only cap at 50/100
  - Ollama integration tested and working
- **Scoring Service**: Combines P&D detection + scoring
  - Full pipeline working

**Phase 4: API & Frontend** ✓
- **Discovery API**: `GET /api/v1/discover` endpoint
  - Returns ranked opportunities with scores (0-100)
  - Filters by minimum score, maximum results
  - Execution time: **0.2 seconds**
- **Discovery Frontend Page**: `/discovery`
  - Displays ranked opportunities with scores
  - Shows sentiment, reasoning, signal counts
  - Highlights pump-and-dump risks

### 2. Trade List Management (NEW)

**Backend API** ✓
- `POST /api/v1/trade-list/add` - Add opportunity to watchlist
- `GET /api/v1/trade-list/` - View trade list with filters
- `DELETE /api/v1/trade-list/{id}` - Remove from list
- `PUT /api/v1/trade-list/{id}/status` - Update status (watchlist/trading/closed)
- `GET /api/v1/trade-list/summary` - Get summary statistics

**Frontend Pages** ✓
- **Discovery Page** (`/discovery`): Added "📝 Add to Trade List" button to each opportunity
- **Trade List Page** (`/trade-list`): New page to manage trading opportunities
  - View watchlist, active trades, and closed trades
  - Update status (start trading, close)
  - Remove opportunities
  - Summary statistics (total opportunities, average score, etc.)

**Database Model** ✓
- New `TradingOpportunity` model with fields:
  - symbol, score, sentiment, reasoning
  - source_count, signal_count, flags
  - status (watchlist/trading/closed)
  - Unique constraint to prevent duplicates

### 3. Removed Google News as News Source

**Reason**: Google News RSS feeds returning 503 errors consistently

**Changes Made**:
- Disabled all Google News RSS sources in `backend/config/news_sources.py`
- Removed Google News resolver calls in `backend/services/data_ingestion/worker.py`
- System now uses only direct RSS feeds (10 sources enabled):
  - CNBC, Yahoo Finance, Seeking Alpha, MarketWatch, Investing.com
  - CoinDesk, Cointelegraph, Bitcoin Magazine
  - Federal Reserve, SEC
  - (Reuters, Bloomberg, ECB, etc. disabled due to no free RSS)

### 4. Bug Fixes

**Fixed: Auto-Analysis Scheduler NameError** ✓
- Added missing `request_id` generation in `_auto_analysis_scheduler_loop`
- Prevents `NameError: name 'request_id' is not defined` error

**Added: Clear Analysis Lock Endpoint** ✓
- New endpoint: `POST /api/v1/admin/clear-analysis-lock`
- Allows clearing stale analysis locks when runs crash or get stuck
- Returns success/error message

---

## Test Results

### Phase 1-3 Tests:
- ✅ 31/31 unit tests passing (ticker extraction)
- ✅ 3/3 integration tests passing (P&D + scoring)
- ✅ Full pipeline test with real data successful

### Real-World Validation:
- ✅ 174-221 tickers discovered from news database
- ✅ 4 SEC insider signals fetched
- ✅ 101 tickers aggregated and scored
- ✅ Execution time: **0.2 seconds** (very fast!)

### Discovery API Test:
```
GET /api/v1/discover?min_score=30&max_results=10
Status: 200 OK
Response:
  - Total articles processed: 100
  - Tickers discovered: 174
  - Opportunities found: 3
  - Execution time: 0.2s
  - Top opportunity: TSM (55/100, insider catalyst)
```

---

## Database Changes

### New Table: `trading_opportunities`
```sql
CREATE TABLE trading_opportunities (
  id INTEGER PRIMARY KEY,
  symbol VARCHAR(10) NOT NULL,
  score INTEGER NOT NULL,
  sentiment VARCHAR(20) NOT NULL,
  reasoning TEXT,
  source_count INTEGER DEFAULT 0,
  signal_count INTEGER DEFAULT 0,
  is_pump_and_dump BOOLEAN DEFAULT 0,
  flags JSON DEFAULT '[]',
  sources JSON DEFAULT '[]',
  added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  status VARCHAR(20) DEFAULT 'watchlist',
  notes TEXT,
  UNIQUE(symbol, status)
);
```

### Migration:
Run `python backend/database/migrate.py` before starting the backend.

---

## Configuration Changes

### Recommended `logic_config.json` Additions:
```json
{
  "auto_discovery": {
    "enabled": true,
    "min_score": 30,
    "max_daily_discoveries": 20,
    "sources": {
      "reddit": {
        "enabled": false,
        "client_id": "",
        "client_secret": ""
      },
      "sec_insider": {
        "enabled": true
      }
    }
  }
}
```

### Environment Variables (Optional):
- `REDDIT_CLIENT_ID` - For Reddit signal source
- `REDDIT_CLIENT_SECRET` - For Reddit signal source

---

## Files Created/Modified

### New Files (Backend):
1. `backend/services/data_ingestion/ticker_extractor.py`
2. `backend/services/data_ingestion/reddit_client.py`
3. `backend/services/data_ingestion/sec_insider_client.py`
4. `backend/services/analysis/signal_aggregator.py`
5. `backend/services/risk/pump_dump_detector.py`
6. `backend/services/scoring/advanced_scorer.py`
7. `backend/services/scoring/ollama_client.py`
8. `backend/services/scoring/scoring_service.py`
9. `backend/routers/discovery.py`
10. `backend/routers/trade_list.py`

### New Files (Frontend):
11. `frontend/src/app/discovery/page.tsx`
12. `frontend/src/app/trade-list/page.tsx`

### Modified Files:
- `backend/database/models.py` (added TradingOpportunity model)
- `backend/main.py` (registered discovery and trade_list routers)
- `backend/config/news_sources.py` (removed Google News sources)
- `backend/services/data_ingestion/worker.py` (removed Google News resolver)
- `backend/routers/config.py` (added clear-analysis-lock endpoint)

### Test Files:
- `backend/tests/test_ticker_extractor.py` (31 tests)
- `backend/tests/test_phase3_integration.py` (3 tests)

---

## Next Steps (Optional Enhancements)

1. **Get Reddit API credentials** - Enables Reddit signal source (high-value)
2. **Add more signal sources** - Twitter, StockTwits, Options flow
3. **Improve scoring accuracy** - Train ML model on historical data
4. **Add fundamentals data** - Fetch price, market cap, short interest
5. **Production deployment** - Add authentication, rate limiting, caching

---

## Breaking Changes

None - All changes are backward compatible.

---

## Upgrade Instructions

1. Pull the latest code
2. Run database migration: `python backend/database/migrate.py`
3. Restart the backend server
4. (Optional) Configure Reddit API credentials for enhanced signals
5. Visit `/discovery` to see automatically discovered opportunities

---

# Release Notes — 2026-07-24

## Fix: Google News RSS 503 Service Unavailable Errors

Google News RSS feeds started returning 503 Service Unavailable errors, causing spam in logs and failed news ingestion.

### Root Cause
Google is deprecating/throttling their RSS search API (`news.google.com/rss/search`). All requests return 503 errors.

### Changes Made

**1. Disabled all Google News RSS sources** (`backend/config/news_sources.py`)
- Set `enabled=False` on all Google News RSS sources (18 sources across 12 categories)
- Categories affected: markets, forex, central_banks, bonds, commodities, economic, ipo, derivatives, fintech, institutional, gcc, analysis
- Added working alternative RSS feeds:
  - Financial Times: `https://www.ft.com/rss/world`
  - BBC Business: `http://feeds.bbci.co.uk/news/business/rss.xml`
  - Al Jazeera Business: `https://www.aljazeera.com/xml/rss/all.xml`

**2. Added graceful 503 handling** (`backend/services/data_ingestion/parser.py`)
- RSS parser now detects 503 errors and logs them as `[SKIPPED]` instead of `[ERROR]`
- Prevents log spam and allows system to continue operating

**3. Added graceful 503 handling** (`backend/services/web_research.py`)
- `_fetch_query_results()` now catches 503 errors and returns empty results
- Prevents web research from crashing when Google News RSS fails
- Logs skipped queries with `[SKIPPED]` prefix

**4. Updated documentation** (`backend/config/news_sources.py`)
- Updated module docstring to explain Google News RSS deprecation
- Marked all Google News sources as disabled until alternatives found

### Config Changes
- `logic_config.json`: No changes
- News sources: 14 direct RSS feeds now enabled, 18 Google News feeds disabled

### Testing
- Verified all edited files have valid Python syntax (`py_compile`)
- Enabled sources count: 14 (all direct RSS, no Google News)

---

## Release Notes — 2026-07-22

## Fix: Backend Test Failures (Groups A-E)

Fixed all 8 backend test failures. Tests now pass (93 passed, 2 skipped).

### Decision: min_same_day_exit_edge_pct = 0.5%
- Changed from 1.0% (config) / 0.0% (test) to 0.5%
- Rationale: Avoid premature exits on noise, but allow exits if meaningful edge exists

### Groups Fixed

**Group A — Missing test dependency (2 fixes)**
- Installed `pytest-asyncio` for async test support
- Created `backend/pytest.ini` with `asyncio_mode = auto`
- Marked `test_edgar_llm_read.py` tests to skip (they hit live SEC EDGAR API)

**Group B — Wrong path assertion (1 fix)**
- Fixed `test_edgar_smoke.py::test_api_imports` to build FastAPI app and test prefixed routes
- Tests now verify `/api/v1/edgar/filings`, `/api/v1/edgar/poll`, etc. are properly wired

**Group C — Module-level state leaking (2 fixes)**
- Added `backend/tests/conftest.py` with autouse fixture to clear module-level dicts
- Resets `_cron_overlap_keys`, `_cron_overlap_underlying_keys`, `_last_order_times` between tests
- Fixes `test_spy_short_leverage_upgrade_is_not_treated_as_direction_flip` and `test_min_same_day_exit_edge_does_not_block_same_day_loss_cut`

**Group E — Proxy terms check and env var cleanup (2 fixes)**
- Fixed `_build_symbol_specific_news_context` to warn when posts don't match proxy terms
- Fixed `test_secret_store.py` to clear environment variable fallbacks

### Config Changes
- Added `pytest-asyncio>=0.21.0` to `requirements.txt`
- Created `backend/pytest.ini` for pytest configuration
- `logic_config.json`: `min_same_day_exit_edge_pct` changed from 1.0 to 0.5

---

## Fix: Trade Retention Decoupled from Analysis Retention — Forward-Return Horizons Now Reachable

The `/alpha` analytics (Information Coefficient, attribution, sensitivity/perturbation) showed no data for any horizon beyond `1h`. Root cause was not in the alpha code at all: `prune_saved_analyses` hard-deleted every `Trade`, `TradeSnapshot`, and `TradeExecution` belonging to any analysis outside the newest `snapshot_retention_limit` runs (default **12**). At a 30-minute analysis cadence that is a ~6 hour window, so a `1d`/`3d`/`1w` snapshot could never resolve before its parent trade was erased.

Evidence at time of diagnosis: 70,216 `decision_log_symbol` rows, but only **6 trades** and **5 snapshots — all `1h`**, with trade IDs already past 457 (≈450 trades created and destroyed).

### What Changed
- **`prune_saved_analyses` no longer touches trade history** — it prunes only `AnalysisResult` and `TradingSignal`. Analysis snapshots are large blobs worth pruning aggressively; trade rows are tiny and are the only record of realized forward returns.
- **New `prune_stale_trades`** — time-based retention keyed on `Trade.recommended_at`, so the window is expressed in the same units as the horizons it must outlive. Deletes in chunks of 500 to stay under SQLite's variable limit, and also cleans `TradeClose` (which the previous code leaked).
- **New `trade_retention_days` config** — default **90**, clamped to 14–3650. The 14-day floor guarantees the window always exceeds the longest tracked horizon (`1w`), so a trade can never be deleted before its final snapshot has had a chance to resolve.
- **Trades may now outlive their parent analysis**, leaving a dangling `analysis_id`. Safe here: SQLite FKs are not enforced (no `PRAGMA foreign_keys=ON`), and the only reader walks analysis → trade, so orphans are simply not listed.

### Perturbation / Sensitivity Panel
- **Added the `1h` horizon** to `_VALID_HORIZONS`, the perturbation defaults, and the IC fetch — previously the only horizon with data was excluded from every query, so the panel filtered to zero rows and rendered `—` everywhere.
- **`sample_count_by_horizon`** per scenario and **`snapshots_available_by_horizon`** at top level — an empty join and a genuine zero return are no longer displayed identically. The UI now shows `no data` vs `+1.23% (n=5)`.
- **Surfaced the 500-row sampling cap** via `rows_available` / `sample_limit` / `sample_truncated` — the panel reported "160 signals fired" when it had silently sampled 500 of 70,216 eligible rows.
- **Banner naming horizons with zero matured snapshots**, explaining that trade rows are pruned once outside the retention window.

### Fix: `/alpha` White-Screen on Backend Hiccup
`load()` called `.json()` without checking `res.ok`, so the BFF's 503 error body (`{error: ...}`) was stored as chart data and `attrData.by_event_type.length` threw `Cannot read properties of undefined`. Any backend blip took the entire page down. Now surfaces an explicit "Backend unreachable" message, with defensive optional chaining at the `attrData` render sites.

### Known Limitation
The three perturbation scenarios currently report identical signal counts. This is correct, not a bug: the sampled `blended_directional_score` values cluster on a small set of discrete values and none fall inside the ±N% band swept around the live entry threshold, so no row changes side. Making the test genuinely sensitive would require perturbing `policy_signal_threshold` or logging the pre-gate continuous score — deferred.

### Files Changed
- `backend/services/analysis/persistence_service.py` — decoupled pruning; added `prune_stale_trades`
- `backend/services/app_config.py` — `trade_retention_days` + `DEFAULT`/`MIN`/`MAX` constants, load/update/payload plumbing
- `backend/database/models.py` — `AppConfig.trade_retention_days` column
- `backend/database/migrate.py` — additive `trade_retention_days` migration
- `backend/services/analysis/perturbation.py` — `1h` horizon, `SAMPLE_LIMIT`, sample/availability counts
- `backend/routers/alpha.py` — `1h` in `_VALID_HORIZONS` and perturbation defaults
- `frontend/src/app/alpha/page.tsx` — response-driven horizons, no-data labelling, sampling banner, 503 crash fix
- `.gitignore` — ignore `backend.log` and stray `nul`

### Config Changes
- `app_config.trade_retention_days` — new, default `90`, range 14–3650. No `logic_config.json` changes.

### Migration
Run `python backend/database/migrate.py` before starting the backend.

---


