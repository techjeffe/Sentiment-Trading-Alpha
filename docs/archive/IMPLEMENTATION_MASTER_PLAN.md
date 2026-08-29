# Master Implementation Plan: Automatic Ticker Discovery
## SignalScope Integration into Sentiment Trading Alpha

**Started:** 2026-07-25  
**Goal:** Complete end-to-end automatic ticker discovery system  
**Approach:** Phase-by-phase with testing at each step

---

## Progress Tracker

- [x] **Phase 1:** Ticker Extraction Module
  - [x] 1.1 Create `ticker_extractor.py`
  - [x] 1.2 Unit tests for ticker extraction (31/31 passing)
  - [x] 1.3 Integrate with existing news ingestion (database schema updated)
  - [x] 1.4 Test with real news articles (integration test passed)
  - [x] 1.5 Update database schema (migration successful)
  - [x] 1.6 **Test with real database content (50 articles processed, 221 tickers discovered)**
  
- [x] **Phase 2:** Signal Sources (Reddit + SEC Insider)
  - [x] 2.1 Port Reddit scraper to Python (created reddit_client.py, requires API credentials)
  - [x] 2.2 Port SEC insider trading fetcher (created sec_insider_client.py, working!)
  - [x] 2.3 Create signal aggregation service (created signal_aggregator.py, working!)
  - [x] 2.4 Test both sources independently (SEC insider tested, Reddit requires credentials)
  - [x] 2.5 Integration test: aggregate signals (tested, 2 tickers aggregated from 3 signals)
  
- [x] **Phase 3:** Scoring & Risk Management
  - [x] 3.1 Port advanced scoring system (created advanced_scorer.py with heuristic scoring)
  - [x] 3.2 Port pump-and-dump detector (created pump_dump_detector.py with 11-flag system)
  - [x] 3.3 Create opportunity ranker (created scoring_service.py combining P&D + scoring)
  - [x] 3.4 Test scoring with sample data (PASSED - heuristic scoring works)
  - [x] 3.5 Test P&D detection (PASSED - correctly flags pumps)
  - [x] 3.6 Integrate with Ollama (created ollama_client.py, Ollama integration working)
  - [x] 3.7 Full integration test (PASSED - all 3 tests passed)
  - [x] 3.8 Fix dataclass compatibility issues (Fixed RawSignal dict access throughout)
  
- [ ] **Phase 4:** API & User Workflow Changes
  - [ ] 4.1 Create `/api/v1/discover` endpoint
  - [ ] 4.2 Modify existing analysis endpoint
  - [ ] 4.3 Frontend opportunity dashboard
  - [ ] 4.4 End-to-end integration test
  
- [ ] **Phase 5:** Configuration & Documentation
  - [ ] 5.1 Update `logic_config.json`
  - [ ] 5.2 Update `REFERENCE.md`
  - [ ] 5.3 Update `RELEASENOTES.md`
  - [ ] 5.4 Create user documentation

---

## Architectural Decision Points

### Decision 1: Database Strategy (Phase 1.5)
**Context:** We need to store discovered tickers and signals

**Option A: Extend Existing SQLite Database**
- Add new tables to existing `trading_system.db`
- Use current `database/models.py` pattern
- Pros: No migration needed, keeps single DB
- Cons: SQLite may not handle high-volume signal data well

**Option B: Add PostgreSQL Alongside SQLite**
- Keep SQLite for existing trading data
- Add PostgreSQL for signal/opportunity data
- Pros: Better for high-volume, matches SignalScope's architecture
- Cons: More complex setup, two databases to manage

**Recommendation:** Option A (SQLite extension) for now - simpler, matches current architecture

---

### Decision 2: Async vs Sync Signal Fetching (Phase 2)
**Context:** Reddit/SEC APIs require HTTP requests

**Option A: Async (asyncio + aiohttp)**
- Matches FastAPI's async pattern
- Better performance for multiple sources
- Pros: Fast, non-blocking
- Cons: More complex error handling

**Option B: Sync (requests library)**
- Simpler to port from SignalScope's TypeScript
- Run in thread pool if needed
- Pros: Simpler, easier to debug
- Cons: Blocking, slower

**Recommendation:** Option A (Async) - future-proof, better performance

---

### Decision 3: Signal Storage Format (Phase 2.3)
**Context:** How to store raw signals before aggregation

**Option A: Store as JSON in SQLite**
- Single column with JSON blob
- Flexible schema
- Pros: Easy to implement, flexible
- Cons: Harder to query, no type safety

**Option B: Normalized Tables**
- Separate tables for signals, sources, tickers
- Proper relationships
- Pros: Queryable, type-safe, matches SignalScope's Prisma schema
- Cons: More tables, more complex

**Recommendation:** Option B (Normalized) - better for analysis and querying

---

## Testing Strategy

### Phase 1 Testing
```bash
# Unit tests
pytest backend/tests/test_ticker_extractor.py -v

# Integration test
python backend/tests/test_ticker_extraction_integration.py
```

### Phase 2 Testing
```bash
# Test Reddit scraper (requires API credentials)
python backend/tests/test_reddit_scraper.py

# Test SEC insider fetcher
python backend/tests/test_sec_insider.py

# Test signal aggregation
python backend/tests/test_signal_aggregation.py
```

### Phase 3 Testing
```bash
# Test scoring system
python backend/tests/test_scoring.py

# Test P&D detection
python backend/tests/test_pump_dump.py
```

### Phase 4 Testing
```bash
# Test API endpoint
curl http://localhost:8000/api/v1/discover

# Test auto-discovery in analysis
curl -X POST http://localhost:8000/api/v1/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"symbols": []}'
```

---

## Phase 1: Ticker Extraction Module

### Step 1.1: Create ticker_extractor.py

**File:** `backend/services/data_ingestion/ticker_extractor.py`

Porting from: `signalscope/src/lib/harvester/sources/ticker-utils.ts`

Key functions to implement:
- `extract_tickers(text: str) -> List[str]`
- `extract_tickers_from_article(title: str, content: str) -> List[str]`
- `is_valid_ticker(ticker: str) -> bool`

Blacklist and mega-caps sets from SignalScope.

---

### Step 1.2: Unit Tests

**File:** `backend/tests/test_ticker_extractor.py`

Test cases:
- Basic ticker extraction
- Blacklist filtering
- Mega-cap filtering
- Edge cases (lowercase, numbers, etc.)

---

### Step 1.3: Integration with News Ingestion

**Modify:** `backend/services/data_ingestion/parser.py`

Add ticker extraction to existing news parsing pipeline.

---

### Step 1.4: Test with Real Articles

**Create:** `backend/tests/test_ticker_extraction_integration.py`

Fetch real news articles and verify ticker extraction works.

---

### Step 1.5: Database Schema Update

**Modify:** `backend/database/models.py`

Add tables:
- `discovered_tickers` - Track ticker discoveries over time
- `article_tickers` - Link tickers to news articles

Run migration: `python backend/database/migrate.py`

---

## Next Steps After Phase 1

Once Phase 1 is complete and tested:
1. Commit changes with clear message
2. Update progress tracker (mark Phase 1 complete)
3. Begin Phase 2 (Reddit + SEC insider sources)
4. Ask for approval before proceeding

---

## Questions for User

1. **Database Decision:** Should we extend SQLite (Option A) or add PostgreSQL (Option B)?
2. **API Credentials:** Do you have Reddit API credentials for Phase 2?
3. **Testing:** Should I create a test database or use the existing one?

---

**Let's begin with Phase 1.1: Creating the ticker extraction module.**
