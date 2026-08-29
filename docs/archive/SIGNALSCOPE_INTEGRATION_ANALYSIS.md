# SignalScope Integration Analysis
## Enhancing Sentiment Trading Alpha with SignalScope's Capabilities

**Date:** 2026-07-25  
**Purpose:** Identify valuable components from signalscope that can improve Sentiment Trading Alpha

---

## Executive Summary

SignalScope is a sophisticated stock breakout signal detection platform with several advanced features that Sentiment Trading Alpha could adopt. The most valuable integrations fall into four categories:

1. **Signal Source Diversification** - Add 8 new signal sources beyond news/EDGAR
2. **Risk Management** - World-class pump-and-dump detection system
3. **Scoring Intelligence** - ML-backed scoring with hard-rule overrides
4. **Infrastructure Improvements** - Better auth, alerts, and frontend components

---

## Current Capability Comparison

### Sentiment Trading Alpha (Current)
- **Signal Sources:** News RSS feeds, Truth Social, SEC EDGAR filings
- **Analysis:** Geopolitical sentiment via 2-stage LLM pipeline
- **Scoring:** Basic sentiment scoring (bullish/bearish/neutral)
- **Risk Management:** Limited (basic keyword filtering)
- **Database:** SQLite with direct SQL queries
- **Frontend:** Next.js with basic admin/health pages
- **Authentication:** None (admin UI only)
- **Backtesting:** Python-based backtesting service

### SignalScope (Available)
- **Signal Sources:** 8 sources (Reddit, X/Twitter, StockTwits, SEC insider, Congress, Volume spikes, Options flow, Polymarket)
- **Analysis:** AI scoring with hard-rule overrides + pump-and-dump detection
- **Scoring:** Opportunity Score + AI Score with ML-validated thresholds
- **Risk Management:** 11-flag pump-and-dump detector with AI validation
- **Database:** PostgreSQL with Prisma ORM
- **Frontend:** Full dashboard with signal cards, trending, portfolio tools
- **Authentication:** NextAuth with multiple providers
- **Backtesting:** LightGBM pipeline with forward return tracking

---

## High-Value Integration Opportunities

### 1. **Signal Source Expansion** (HIGH PRIORITY)

**What SignalScope Has:**
- `src/lib/harvester/sources/` contains 8 signal source integrations:
  - `reddit.ts` - 17 subreddits with velocity scoring
  - `twitter.ts` - X/Twitter search with bot detection
  - `stocktwits.ts` - StockTwits messages and sentiment
  - `sec-insider.ts` - SEC Form 4 insider trading
  - `congress.ts` - Congressional trading disclosures
  - `options-flow.ts` - Unusual options activity
  - `volume-spike.ts` - Volume spike detection
  - `polymarket.ts` - Polymarket prediction markets

**Benefit to Sentiment Trading Alpha:**
- Diversify signal sources beyond news/geopolitical events
- Add "smart money" signals (insider trading, congressional trades, options flow)
- Capture retail sentiment (Reddit, StockTwits)
- Detect early breakout signals via volume spikes

**Implementation Approach:**
- Port the TypeScript source integrations to Python
- Integrate into `backend/services/data_ingestion/`
- Add configuration in `logic_config.json` to enable/disable sources
- Store signals in new SQLite tables (or migrate to PostgreSQL)

---

### 2. **Pump-and-Dump Detection System** (HIGH PRIORITY)

**What SignalScope Has:**
- `src/lib/harvester/pnd-filter.ts` - 11-flag detection system:
  - `penny_price` - Price < $0.50 without catalyst
  - `sub_dime_52wk_floor` - 52-week low < $0.09
  - `upvote_pump` - Disproportionate upvotes vs comments
  - `otc_listing` - OTC/Pink sheet listings
  - `micro_cap_no_catalyst` - Market cap < $40M without news
  - `only_penny_subs` - Only in r/pennystocks, not reputable subs
  - `single_source` - Only one source, no corroboration
  - `hyperbolic_language` - "10x", "guaranteed", etc.
  - `coordinated_posts` - Identical phrasing across posts
  - `no_news_catalyst` - 5+ signals but no real catalyst
  - `sudden_spike` - All posts < 3 hours old
  - `twitter_bot_promoters` - Bot-like Twitter accounts
  - `twitter_coordinated_pump` - Coordinated Twitter pump

**Benefit to Sentiment Trading Alpha:**
- **Critical risk management** - Prevent entering pump-and-dump schemes
- ML-validated flags (backtested on historical data)
- AI-assisted borderline cases (calls LLM for 2-flag tickers)
- Informational flags vs effective flags (some flags are bearish but not P&D)

**Implementation Approach:**
- Port the 11-flag logic to Python
- Create `backend/services/risk/pump_dump_detector.py`
- Integrate into the analysis pipeline before position sizing
- Add P&D flags to the frontend UI

---

### 3. **Advanced Scoring System** (MEDIUM-HIGH PRIORITY)

**What SignalScope Has:**
- `src/lib/harvester/scoring.ts` - AI batch scoring with:
  - **Hard-rule overrides** - Social-only signals capped at 50/100
  - **Catalyst weighting** - Insider/options/congress boost scores
  - **Velocity scoring** - Signal momentum (rising/hot/comment-derived)
  - **Novelty adjustment** - First appearance vs stale signals
  - **Source corroboration** - Multi-source boost (+3 to +8)
  - **Engagement quality** - Upvote/comment ratio analysis
  - **ML-informed penalties** - Stale signals, high comment count

- `src/lib/harvester/opportunity-score.ts` - Early-mover ranking

**Benefit to Sentiment Trading Alpha:**
- Replace basic sentiment scoring with multi-factor scoring
- Prevent overconfidence in social media hype
- Weight "smart money" signals appropriately
- Add opportunity score for early breakout detection

**Implementation Approach:**
- Create `backend/services/scoring/advanced_scorer.py`
- Integrate with existing LLM pipeline (use as Stage 3)
- Add scoring factors to `logic_config.json`
- Return both sentiment AND opportunity scores

---

### 4. **Signal Aggregation & Velocity Tracking** (MEDIUM PRIORITY)

**What SignalScope Has:**
- `src/lib/harvester/index.ts` - Signal aggregation:
  - `aggregateSignals()` - Groups by symbol, calculates velocity
  - Velocity tiers: rising=3, hot=2, comment=1.5, fresh=2, recent=1
  - Momentum breakdown: risingCount, freshCount, staleCount
  - Source count, subreddit count, engagement metrics

**Benefit to Sentiment Trading Alpha:**
- Track signal momentum over time
- Detect emerging consensus (Early → Forming → Confirmed)
- Identify stale signals (already played out)
- Better position sizing based on signal freshness

**Implementation Approach:**
- Add signal aggregation to `backend/services/analysis/signal_service.py`
- Create velocity tracking in the database
- Add momentum indicators to the analysis output

---

### 5. **Frontend Component Library** (MEDIUM PRIORITY)

**What SignalScope Has:**
- `src/lib/signal-card-shared.tsx` - Reusable signal card component
- `src/lib/signal-row-grid.ts` - Grid layout for signal lists
- `src/lib/stage-labels.ts` - Stage visualization (Early/Forming/Confirmed)
- `src/app/(dashboard)/ticker/[symbol]/page.tsx` - Detailed ticker view
- `src/app/(dashboard)/trending/page.tsx` - Cross-scan trending view

**Benefit to Sentiment Trading Alpha:**
- Professional UI for signal visualization
- Ticker detail pages with charts
- Trending view across multiple scans
- Better mobile responsiveness

**Implementation Approach:**
- Adapt TypeScript components to Sentiment Trading Alpha's frontend
- Add ticker detail pages to `frontend/src/app/`
- Create signal card components in `frontend/src/components/`

---

### 6. **Authentication & User Management** (LOW-MEDIUM PRIORITY)

**What SignalScope Has:**
- `src/lib/auth.ts` - NextAuth configuration
- `src/lib/auth.config.ts` - Auth providers (credentials, Google, etc.)
- User roles (admin, user)
- Protected API routes with session checks

**Benefit to Sentiment Trading Alpha:**
- Multi-user support
- Protected API endpoints
- User-specific watchlists and alerts
- Admin vs regular user permissions

**Implementation Approach:**
- Integrate NextAuth into Sentiment Trading Alpha's frontend
- Add user tables to database
- Protect API routes with session checks

---

### 7. **Email Alerts & Notifications** (LOW-MEDIUM PRIORITY)

**What SignalScope Has:**
- `src/lib/email/` - Resend integration
- `src/app/api/alerts/` - Alert endpoints
- Weekly digest emails
- Portfolio alerts

**Benefit to Sentiment Trading Alpha:**
- Notify users of new high-confidence signals
- Email daily/weekly summaries
- Alert on position changes

**Implementation Approach:**
- Add Resend to `frontend/package.json`
- Create alert services in backend
- Add email preferences to user settings

---

### 8. **PostgreSQL + Prisma Migration** (LOW PRIORITY - ARCHITECTURAL)

**What SignalScope Has:**
- `prisma/schema.prisma` - Full database schema
- `src/lib/prisma.ts` - Prisma client
- Migrations in `prisma/migrations/`

**Benefit to Sentiment Trading Alpha:**
- Better ORM than raw SQL
- Type-safe database queries
- Easier schema migrations
- Better relationships and constraints

**Implementation Approach:**
- **Not recommended immediately** - SQLite works fine for single-user
- Consider if adding multi-user support or complex relationships
- Could port schema to SQLite with Prisma (Prisma supports SQLite)

---

## Implementation Roadmap

### Phase 1: Core Signal Sources (Week 1-2)
1. Port Reddit signal scraper to Python
2. Add SEC insider trading fetcher
3. Add congressional trading disclosures
4. Create new database tables for signals
5. Integrate into existing ingestion pipeline

### Phase 2: Pump-and-Dump Detection (Week 3)
1. Port P&D filter flags to Python
2. Add to analysis pipeline
3. Create P&D API endpoint
4. Display P&D flags in frontend

### Phase 3: Advanced Scoring (Week 4)
1. Create advanced scorer service
2. Integrate with LLM pipeline
3. Add scoring configuration to `logic_config.json`
4. Update frontend to show scores

### Phase 4: Frontend Enhancements (Week 5-6)
1. Add ticker detail pages
2. Create signal card components
3. Add trending view
4. Improve mobile responsiveness

### Phase 5: Nice-to-Haves (Future)
1. Add authentication
2. Email alerts
3. Options flow integration
4. Twitter/StockTwits integration

---

## Files to Port (Priority Order)

### High Priority
```
signalscope/src/lib/harvester/pnd-filter.ts → backend/services/risk/pump_dump_detector.py
signalscope/src/lib/harvester/scoring.ts → backend/services/scoring/advanced_scorer.py
signalscope/src/lib/harvester/sources/reddit.ts → backend/services/data_ingestion/reddit_client.py
signalscope/src/lib/harvester/sources/sec-insider.ts → backend/services/data_ingestion/sec_insider_client.py
signalscope/src/lib/harvester/sources/congress.ts → backend/services/data_ingestion/congress_client.py
signalscope/src/lib/harvester/types.ts → backend/services/data_ingestion/types.py
```

### Medium Priority
```
signalscope/src/lib/harvester/index.ts (aggregateSignals function) → backend/services/analysis/signal_aggregator.py
signalscope/src/lib/harvester/opportunity-score.ts → backend/services/scoring/opportunity_score.py
signalscope/src/lib/signal-card-shared.tsx → frontend/src/components/SignalCard.tsx
signalscope/src/lib/stage-labels.ts → frontend/src/lib/stage-labels.ts
```

### Low Priority
```
signalscope/src/lib/harvester/sources/options-flow.ts → backend/services/data_ingestion/options_flow_client.py
signalscope/src/lib/harvester/sources/twitter.ts → backend/services/data_ingestion/twitter_client.py
signalscope/src/lib/email/* → backend/services/email/
signalscope/src/lib/auth.ts → frontend/src/lib/auth.ts
```

---

## Configuration Changes Needed

### logic_config.json Additions
```json
{
  "signal_sources": {
    "reddit": {
      "enabled": true,
      "subreddits": ["wallstreetbets", "stocks", "investing"],
      "max_posts_per_sub": 25
    },
    "sec_insider": {
      "enabled": true,
      "min_value": 10000
    },
    "congress": {
      "enabled": true,
      "chamber": ["house", "senate"]
    }
  },
  "pump_dump_detection": {
    "enabled": true,
    "threshold": 3,
    "flags": {
      "penny_price": true,
      "otc_listing": true,
      "micro_cap_no_catalyst": true
    }
  },
  "scoring": {
    "use_advanced_scoring": true,
    "social_only_cap": 50,
    "catalyst_source_boost": 15
  }
}
```

---

## Risks & Mitigations

### Risk 1: Over-Engineering
**Risk:** Adding too many features too quickly  
**Mitigation:** Phase implementation, start with 1-2 signal sources

### Risk 2: Python vs TypeScript Porting
**Risk:** Bugs when porting TypeScript to Python  
**Mitigation:** Write unit tests for each ported module

### Risk 3: Database Migration Complexity
**Risk:** Breaking existing SQLite database  
**Mitigation:** Use `migrate.py` for safe schema additions

### Risk 4: API Rate Limits
**Risk:** Reddit/Twitter API limits  
**Mitigation:** Add rate limiting, caching, and graceful degradation

---

## Success Metrics

After integration, measure:
1. **Signal quality** - % of signals that generate positive returns
2. **P&D detection** - % of flagged tickers that would have lost money
3. **Source diversification** - # of unique signal sources per ticker
4. **User engagement** - Time spent on new ticker detail pages
5. **False positive reduction** - Fewer bad trades due to P&D filtering

---

## Next Steps

1. **Review this document** with the team
2. **Create GitHub Issues** for each phase
3. **Start with Phase 1** - Port Reddit scraper first
4. **Test thoroughly** with paper trading before real trades
5. **Document all changes** in RELEASENOTES.md

---

## Appendix: Key SignalScope Files Reference

### Core Harvester System
- `src/lib/harvester/index.ts` - Main harvest orchestration
- `src/lib/harvester/types.ts` - TypeScript interfaces
- `src/lib/harvester/scoring.ts` - AI scoring
- `src/lib/harvester/pnd-filter.ts` - Pump-and-dump detection
- `src/lib/harvester/recommendation.ts` - Label generation
- `src/lib/harvester/opportunity-score.ts` - Early-mover ranking

### Signal Sources
- `src/lib/harvester/sources/reddit.ts` - Reddit integration (17 subreddits)
- `src/lib/h