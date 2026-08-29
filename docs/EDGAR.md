---
# SEC EDGAR Filings Integration — Design Notes

> Draft spec for adding SEC EDGAR filing polling + LLM summarization to the
> sentiment pipeline. Written to match existing repo conventions documented in
> `docs/REFERENCE.md` / `CLAUDE.md`. **Verify the EDGAR API details below against
> the live docs at https://www.sec.gov/edgar/sec-api-documentation before
> building — they were compiled from general knowledge, not a fresh fetch.**

---

## Part A — SEC EDGAR API primer (NVDA worked example)

SEC EDGAR's modern APIs are all free, unauthenticated JSON/REST endpoints under
`data.sec.gov` and `www.sec.gov`. No API key. The only requirement is a
compliant `User-Agent` header and respecting a soft rate limit.

### A.1 Required `User-Agent` header

Every request **must** include a descriptive `User-Agent` identifying the
requester, e.g.:

User-Agent: Sentiment Trading Alpha admin@yourdomain.com

Generic/browser-spoofed User-Agents (like the one `worker.py` uses for RSS
scraping) will get **403 Forbidden** or an IP ban from EDGAR — this must be a
separate, EDGAR-specific header, not reused from `data_ingestion/worker.py`.

### A.2 Rate limit

SEC's stated guidance: **max ~10 requests/second**, and to avoid bursts.
There's no visible token bucket enforcement below that, but sustained
polling loops should self-throttle (e.g. `asyncio.Semaphore` + a small delay
between requests) — nothing in this repo currently implements request
throttling, so this would be new.

### A.3 Ticker → CIK resolution

GET https://www.sec.gov/files/company_tickers.json

Returns a JSON object keyed by row index, each entry:
```json
{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}

For NVDA, CIK is 1045810. This file should be fetched once and cached
(it's large, ~800KB, and changes infrequently) — a good pairing with
AppConfig.symbol_company_aliases, which already stores ticker→company-name
mappings for search/keyword purposes and could be extended to store the
resolved CIK per tracked symbol.

CIKs are always used zero-padded to 10 digits in data.sec.gov URLs, e.g.
0001045810.

A.4 Submissions API — list of a company's filings

GET https://data.sec.gov/submissions/CIK0001045810.json

Top-level fields include cik, name, tickers, exchanges, sic,
sicDescription, fiscalYearEnd, and a filings object:

{
  "cik": "1045810",
  "name": "NVIDIA CORP",
  "tickers": ["NVDA"],
  "exchanges": ["NASDAQ"],
  "filings": {
    "recent": {
      "accessionNumber": ["0001045810-24-000029", "0001045810-24-000160", ...],
      "filingDate": ["2024-02-21", "2024-05-22", ...],
      "reportDate": ["2024-01-28", "2024-04-28", ...],
      "acceptanceDateTime": ["2024-02-21T16:32:00.000Z", ...],
      "act": ["34", "34", ...],
      "form": ["10-K", "10-Q", "8-K", "4", "SC 13G/A", ...],
      "fileNumber": ["001-23985", ...],
      "items": ["", "2.02,9.01", ...],
      "size": [12345678, ...],
      "isXBRL": [1, 1, ...],
      "isInlineXBRL": [1, 1, ...],
      "primaryDocument": ["nvda-20240128.htm", "nvda-20240428.htm", ...],
      "primaryDocDescription": ["10-K", "10-Q", ...]
    },
    "files": [
      {"name": "CIK0001045810-submissions-001.json", "filingCount": 250, "filingFrom": "2001-01-01", "filingTo": "2020-12-31"}
    ]
  }
}

- Arrays are parallel — index i across all arrays describes one filing.
- form is the filing type: "10-K", "10-Q", "8-K", "4" (insider
transaction), "SC 13G"/"SC 13D" (beneficial ownership), "DEF 14A"
(proxy), etc. Filter this list client-side for the forms you care about
(10-K, 10-Q, 8-K are the obvious starting set for sentiment).
- items — for 8-Ks, comma-separated item codes (e.g. "2.02" = Results of
Operations, "5.02" = officer/director changes, "7.01" = Reg FD
disclosure) — useful for cheap materiality triage before ever calling an
LLM (this is the natural "Stage 0 filter" analog to _matches_stage0_filter
in worker.py).
- Only the most recent ~1000 filings are in filings.recent; older ones are
in the paginated files listed under filings.files (each fetched from
https://data.sec.gov/submissions/<name>). For a live polling use case,
filings.recent is sufficient — you never need the paginated history.

A.5 Filing document retrieval

Given a filing's accessionNumber (e.g. 0001045810-24-000029) and CIK
(1045810), the filing's document index is:

GET https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/index.json

(accession number with dashes stripped in the URL path). This returns the
list of every file in that filing package (primary document, exhibits, XBRL
instance document, rendered R*.htm financial statement pages). The primary
document itself (matching primaryDocument from the submissions API) is
fetched directly:

GET https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda-20240128.htm

This is the actual 10-K HTML document — large (multi-MB), and this is the
raw material for the "have an LLM read/summarize the filing" part of the
feature. Recommended approach: fetch the HTML, strip to text (the repo
already depends on trafilatura for exactly this kind of HTML→text
extraction in worker.py — reuse it here, or use a more filing-specific
approach since SEC HTML has heavy embedded tables), then chunk before
sending to the LLM (10-Ks are typically 50,000–150,000+ words — far beyond
any local model's context window, and beyond most cloud models' comfortable
single-shot summarization budget too). A very common approach: extract only
specific sections (Item 1A Risk Factors, Item 7 MD&A, Item 2.02 earnings
text for 8-Ks) rather than summarizing the whole document.

A.6 Company Facts API — structured XBRL financial data

GET https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json

Returns every XBRL-tagged financial fact NVDA has ever reported, organized
by taxonomy → concept (e.g. us-gaap.Revenues, us-gaap.NetIncomeLoss,
us-gaap.Assets), each concept holding a list of values tagged with
fy, fp (fiscal period, e.g. "Q1"), form, filed date, val, accn
(accession number), start/end dates. This is a structured numeric
data source, analogous to how market_validation.py pulls structured
FRED/EIA numbers rather than free text — if the feature ever wants "flag when
reported revenue misses/beats X" type signals without an LLM, this is the
API to use instead of parsing prose out of the 10-K.

Single-concept variant (smaller payload, one metric across all filings):
GET https://data.sec.gov/api/xbrl/companyconcept/CIK0001045810/us-gaap/Revenues.json

A.7 Full Text Search API

GET https://efts.sec.gov/LATEST/search-index?q=%22NVIDIA%22&forms=10-K&dateRange=custom&startdt=2024-01-01&enddt=2024-12-31

Powers the UI at https://www.sec.gov/edgar/search/. Useful for keyword
search across all filings (any company), less useful than the submissions
API for "poll one tracked company's new filings," but potentially useful for
symbol-agnostic sweep (e.g. "any 8-K industry-wide mentioning tariffs").
Lower priority for v1.

A.8 Per-company Atom/RSS feed (alternative polling mechanism)

GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810&type=10-K&dateb=&owner=include&count=40&output=atom

Returns an Atom feed of filings matching type (form prefix — 10-K also
matches 10-K/A amendments unless filtered further) for one CIK. This
mirrors the existing RSS-feed ingestion pattern (RSSFeedParser in
services/data_ingestion/parser.py) almost exactly, so it's a tempting
"reuse the RSS parser" shortcut — but the JSON submissions API (A.4) is
strictly more capable (structured fields, items, isXBRL, no XML parsing)
and is the recommended primary source. Keep the Atom feed as a fallback
option only if JSON polling proves awkward.

A.9 Practical polling design

Because filings.recent always contains the newest filings first, a poll
cycle is simply:

1. Fetch https://data.sec.gov/submissions/CIK{cik}.json for each tracked
symbol with a resolved CIK.
2. Filter form to the configured set (e.g. {"10-K", "10-Q", "8-K"}).
3. Diff against accessionNumbers already stored in the new sec_filings
table (dedup key) — anything new gets queued for fetch + LLM processing.
4. No true "webhook/push" exists — this is poll-only, same as the existing
RSS/Yahoo ingestion.

---
Part B — Existing repo architecture (from investigation)

(Condensed from a full codebase investigation — see file paths for detail.)

B.1 REFERENCE.md / CLAUDE.md — governing docs

- docs/REFERENCE.md (docs/) is the architecture "bible": project structure
tree, API reference, a running Schema Migration Reference table
(every DB column/table ever added, with default + purpose — append new
EDGAR tables/columns here), and a Validation Sources section
documenting the existing FRED/EIA per-symbol structured-data injection
pattern (the closest existing analog to what filings data should become).
- CLAUDE.md (repo root) is the binding rules file:
  - "Never hardcode thresholds: Weights, thresholds, and holding periods
must be pulled from the configuration service."
  - "Sentiment: Python backend handles the math; the LLM provides
structured facts."
  - "Security: API keys must be handled via keyring. Never log or hardcode
these credentials."
  - "Schema Migrations: Changes must be additive and handle migrate.py
automatically."
  - "Propose changes that maintain the separation between extraction
(Stage 1) and reasoning (Stage 2)."

B.2 Background polling pattern

backend/main.py runs several while True: asyncio background loops
started at app startup (no APScheduler/cron — plain asyncio tasks). The
model to copy: _data_ingestion_scheduler_loop() (main.py line ~52):
DB-driven interval (AppConfig.data_ingestion_interval_seconds), checks the
analysis lock before running, calls the actual worker function, records
outcome via services/runtime_health.py::record_data_pull(...) for the
/health endpoint, sleeps, repeats.

The actual fetch/parse/store logic lives in a separate module:
backend/services/data_ingestion/worker.py::run_ingestion_cycle(). New EDGAR
work should mirror this exact split: a new loop function in main.py
(e.g. _edgar_polling_scheduler_loop()) + a new module
backend/services/data_ingestion/edgar_worker.py with the actual polling
logic, its own DB-configurable interval column (EDGAR filings arrive far
less often than news — a much longer interval, e.g. hourly, makes sense and
should be its own config value, not reuse data_ingestion_interval_seconds).

B.3 Tracked symbols configuration

Single source of truth: AppConfig.tracked_symbols (JSON column) +
AppConfig.custom_symbols, managed through
backend/services/app_config.py::get_or_create_app_config(db) /
update_app_config(db, payload). Built-ins: SUPPORTED_SYMBOLS = ["USO", "IBIT", "QQQ", "SPY"]. AppConfig.symbol_company_aliases alreadymaps
ticker→company name (used for search queries) and is the natural place to
also cache the resolved EDGAR CIK per symbol (or add a parallel
symbol_edgar_ciks: Dict[str, str] JSON column) so CIK resolution
(company_tickers.json) only has to run once per symbol, not every poll
cycle. Note ETFs like SPY/QQQ/USO/IBIT don't file their own 10-Ks/8-Ks in
the same way individual companies do (they're fund vehicles) — this feature
is really most meaningful for custom equity symbols (NVDA, ORCL, etc.), so
CIK resolution should probably only run for non-ETF tracked symbols.

B.4 LLM call structure — Stage 1 (extraction) vs Stage 2 (reasoning)

Central engine: backend/services/sentiment/engine.py::SentimentEngine.
Backend-agnostic (ollama / vllm / openai-compatible), selected via
AppConfig.inference_backend, cloud API keys pulled from
services/secret_store.py (keyring), never hardcoded.

- Stage 1 (extraction) — SentimentEngine.extract_relevant_articles():
cheap, schema-constrained LLM calls (JSON Schema passed via Ollama's
format field / OpenAI's response_format) using config.extraction_model
(a separate, typically smaller model than Stage 2's reasoning_model —
both independently admin-selectable AppConfig columns). For filings, an
analogous Stage-1 step would be: cheap structured extraction from the raw
filing text — e.g. {"filing_type": "10-K", "material_items": [...], "guidance_change": bool, "risk_factor_changes": [...]} — not a full
narrative summary yet.
- Stage 2 (reasoning) — SentimentEngine._analyze_with_context() →
per-symbol specialist prompt
(format_symbol_specialist_context_prompt in services/sentiment/prompts.py),
schema-constrained (build_specialist_response_schema(symbol)), returns
only categorical facts (event_type, confirmed, bluster_count,
substance_count, exposure_type, symbol_relevance, source_count,
trading_type) — no raw floats. All scoring
(bluster_score/policy_score/confidence/directional_score) is
computed in pure Python by SentimentEngine.compute_symbol_scores(),
reading every threshold from config.logic_loader.LOGIC.

Recommended integration point: don't invent a third LLM stage. Have the
EDGAR worker produce a filing summary string (either via a lightweight
Stage-1-style structured extraction call, or a straightforward
"summarize this filing section" call) and feed it into Stage 2 the same way
market_validation.py's FRED/EIA bundle or web_research.py's search
results are already injected as validation_context /
web_research_context into format_symbol_specialist_context_prompt(...).
This keeps the filing content flowing through the existing, already-battle-
tested Stage 2 specialist scoring pipeline instead of building a parallel
scoring path.

B.5 Database schema / migration pattern

No Alembic — hand-written, idempotent, additive-only SQL in
backend/database/migrate.py::migrate():
- Existing table: PRAGMA table_info(<table>) → diff column names →
ALTER TABLE ... ADD COLUMN ... per missing column, each conn.commit()'d
individually.
- New table: check sqlite_master for table existence → raw
CREATE TABLE ... + CREATE INDEX IF NOT EXISTS ... statements.
- Runs automatically on every backend startup; can also run manually via
cd backend && python -m database.migrate.
- Matching SQLAlchemy model required in backend/database/models.py
(Base declarative class, e.g. ScrapedArticle at line 38 is the closest
existing precedent: dedup via url unique + content_hash, processed
boolean + processed_at, indexes on the columns you'll query by).

Proposed new table (sec_filings), following the scraped_articles
precedent:

CREATE TABLE sec_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(10) NOT NULL,
    cik VARCHAR(10) NOT NULL,
    accession_number VARCHAR(24) NOT NULL UNIQUE,
    form_type VARCHAR(20) NOT NULL,          -- "10-K", "10-Q", "8-K", ...
    filing_date DATE NOT NULL,
    report_date DATE,
    items VARCHAR(64),                        -- 8-K item codes, comma-separated
    primary_document_url TEXT NOT NULL,
    raw_text TEXT,                             -- extracted filing text (or key sections)
    llm_summary TEXT,                          -- Stage 1 structured extraction / summary
    processed BOOLEAN NOT NULL DEFAULT 0,
    discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME
);
CREATE INDEX ix_sec_filings_symbol ON sec_filings (symbol);
CREATE INDEX ix_sec_filings_processed ON sec_filings (processed);
CREATE INDEX ix_sec_filings_filing_date ON sec_filings (filing_date);

Append this table to docs/REFERENCE.md's Schema Migration Reference table once
built.

B.6 logic_config.json — "never hardcode thresholds"

backend/config/logic_config.json, loaded once via
backend/config/logic_loader.py (LOGIC dict, e.g.
LOGIC["entry_thresholds"]["normal"]). Two-tier convention: static defaults
in the JSON file (versioned, code-reviewed) + optional live override via a
nullable AppConfig DB column (admin-UI editable without redeploy) — DB
value wins when set. Add a new block for EDGAR, e.g.:

"edgar_filings": {
  "enabled": true,
  "poll_interval_minutes": 60,
  "tracked_form_types": ["10-K", "10-Q", "8-K"],
  "material_8k_items": ["2.02", "5.02", "7.01", "8.01"],
  "max_filing_chars_for_llm": 40000,
  "lookback_days_on_first_poll": 7
}

read via _L["edgar_filings"][...], with matching nullable AppConfig
override columns for anything that should be admin-editable at runtime
(mirrors the existing entry_threshold, materiality_min_posts_delta
nullable-override columns in migrate.py).

B.7 HTTP client conventions

Plain requests throughout (no httpx), synchronous calls wrapped in
asyncio.to_thread(...) when invoked from async code. Best structural
template: backend/services/data_ingestion/market_validation.py's
MarketValidationClient — a class holding requests.Session()
(session.trust_env = False to avoid inheriting broken proxy env vars),
constructor timeout, an in-memory TTL cache dict (_cache,
_cache_ttl_seconds), a public bundle-fetch entrypoint dispatching to
private per-symbol builder methods, and a build_prompt_context(bundle)
static method condensing structured data to prompt text. No retry/backoff
library exists anywhere in the repo (errors degrade gracefully, no retries)
— an EdgarClient can follow the same graceful-degradation style, or add a
small retry helper if EDGAR's occasional 429s warrant it.

Nothing in the repo currently sets a compliant EDGAR-style User-Agent —
worker.py's browser-spoofed UA (for RSS/article scraping) must not be
reused for EDGAR calls; build a separate header for the new EdgarClient,
ideally sourced from an env var / config value (e.g. EDGAR_USER_AGENT)
rather than hardcoded inline.

B.8 Secrets

backend/services/secret_store.py wraps OS keychain via keyring
(Windows Credential Manager / macOS Keychain), with env var fallback for
Docker. EDGAR needs no API key or auth — only the compliant User-Agent
string, which is a contact/compliance identifier, not a secret, so it does
not need keyring storage. This module is only relevant here if a paid
third-party filings API is added later.

B.9 Confirmed: no existing EDGAR code

A full-repo grep for edgar|sec\.gov|10-K|8-K|10-Q|filing found exactly one
hit — backend/services/web_research.py, which only uses site:sec.gov as
a Google-News-RSS search filter string and "SEC" as a trusted-source
label. There is no EDGAR client, CIK resolution, or filing parser anywhere.
This is a greenfield feature.

---
Part C — Suggested build order for your developer

1. backend/services/edgar/client.py — EdgarClient class: CIK resolution
(cache company_tickers.json), get_recent_filings(cik, forms=...) via
the submissions API, fetch_filing_document(cik, accession, filename),
compliant User-Agent, basic rate limiting (semaphore + delay).
2. backend/database/models.py — add SecFiling model.
backend/database/migrate.py — add sec_filings table creation block.
3. backend/config/logic_config.json + logic_loader — add edgar_filings
block; add matching nullable AppConfig override columns if needed.
4. backend/services/app_config.py — extend symbol_company_aliases (or add
symbol_edgar_ciks) so CIK is resolved once per symbol, not every poll.
5. backend/services/data_ingestion/edgar_worker.py — poll cycle: resolve
CIKs for tracked non-ETF symbols → fetch recent filings → diff against
sec_filings.accession_number → fetch new filing documents → extract
text (reuse trafilatura, or targeted section extraction) → store.
6. backend/main.py — new _edgar_polling_scheduler_loop() background task,
same shape as _data_ingestion_scheduler_loop().
7. Stage 1 extraction hook in services/sentiment/engine.py (or a small new
helper) — structured extraction from filing text using
config.extraction_model.
8. Wire the resulting filing summary into
format_symbol_specialist_context_prompt(...)'s existing
validation_context / a new filing_context parameter, alongside FRED/
EIA and web-research context, so Stage 2 scoring picks it up unchanged.
9. Update docs/REFERENCE.md (Schema Migration Reference table + a new "SEC
EDGAR Filings" section) and RELEASENOTES.md per the repo's PR template
in CLAUDE.md.

---
Compiled from a live codebase investigation (file paths cited throughout
Part B) plus general knowledge of SEC EDGAR's public JSON APIs (Part A —
verify against https://www.sec.gov/edgar/sec-api-documentation before
implementation, as web-fetch tools were unavailable at compile time).