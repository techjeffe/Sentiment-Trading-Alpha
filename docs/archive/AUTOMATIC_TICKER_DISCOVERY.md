# Automatic Ticker Discovery - Implementation Plan
## Bringing SignalScope's "Find Stocks For You" to Sentiment Trading Alpha

**Date:** 2026-07-25  
**Priority:** HIGHEST - This changes the core user workflow

---

## The Problem: STA Requires Manual Ticker Selection

### Current STA Workflow (Manual)
```
User: "Analyze USO, QQQ, and SPY"
  ↓
STA: Analyzes only those 3 symbols
  ↓
User must manually discover new opportunities elsewhere
```

**Evidence from code:**
- `backend/routers/analysis.py` line: `symbols = request.symbols or config.tracked_symbols or ["USO", "IBIT", "QQQ", "SPY"]`
- User must specify symbols in the request payload
- Config has `tracked_symbols` but these are still manually curated
- No mechanism to discover *new* tickers

### SignalScope Workflow (Automatic)
```
SignalScope: Scans Reddit, Twitter, SEC filings, etc.
  ↓
Automatically extracts tickers from posts (regex + blacklist)
  ↓
Aggregates signals by ticker (aggregateSignals())
  ↓
Scores and ranks discovered tickers
  ↓
Presents top opportunities to user
```

**Evidence from code:**
- `src/lib/harvester/sources/ticker-utils.ts` - `extractTickers()` function
- `src/lib/harvester/index.ts` - `aggregateSignals()` groups by symbol automatically
- No user input required for ticker discovery

---

## How SignalScope Automatically Discovers Tickers

### Step 1: Extract Tickers from Unstructured Text

**File:** `src/lib/harvester/sources/ticker-utils.ts`

```typescript
export const TICKER_REGEX = /\b([A-Z]{1,5})\b/g;

export const BLACKLIST = new Set([
  // English words that look like tickers
  "A", "I", "AM", "AN", "CEO", "IPO", "ETF", ...
  // Common ETFs (not breakout candidates)
  "SPY", "QQQ", "IWM", "DIA", ...
  // Crypto
  "BTC", "ETH", "SOL", ...
]);

export function extractTickers(text: string): string[] {
  const matches = text.match(TICKER_REGEX) || [];
  return [...new Set(matches.filter((t) => 
    !BLACKLIST.has(t) && 
    !MEGA_CAPS.has(t) && 
    t.length >= 2
  ))];
}
```

**Key Insight:** This regex-based approach finds tickers in:
- Reddit post titles and bodies
- Twitter/X tweets
- SEC filing text
- StockTwits messages
- Options flow descriptions

### Step 2: Aggregate Signals by Ticker

**File:** `src/lib/harvester/index.ts` - `aggregateSignals()` function

```typescript
export function aggregateSignals(signals: RawSignal[]): AggregatedSymbol[] {
  const bySymbol = new Map<string, RawSignal[]>();
  
  // Group all signals by symbol automatically
  for (const signal of signals) {
    const existing = bySymbol.get(signal.symbol) || [];
    existing.push(signal);
    bySymbol.set(signal.symbol, existing);
  }
  
  // Calculate per-symbol metrics
  return [...bySymbol.entries()].map(([symbol, sigs]) => ({
    symbol,
    signals: sigs,
    sourceCount: new Set(sigs.map((s) => s.source)).size,
    totalUpvotes: sigs.reduce((sum, s) => sum + (s.upvotes || 0), 0),
    avgVelocity: /* ... */,
    momentum: /* ... */,
  }));
}
```

### Step 3: Score and Rank Automatically

**File:** `src/lib/harvester/scoring.ts`

- AI batch scoring ranks discovered tickers
- Hard-rule overrides (social-only capped at 50/100)
- Catalyst weighting (insider > options > social)

### Step 4: Filter Out Bad Tickers

**File:** `src/lib/harvester/pnd-filter.ts`

- 11-flag pump-and-dump detection
- Removes scam stocks automatically
- Only presents "safe" opportunities

---

## Implementation Plan for STA

### Phase 1: Port Ticker Extraction (Week 1)

**Goal:** Automatically extract tickers from existing news sources

#### 1.1 Create Ticker Extraction Module

**New File:** `backend/services/data_ingestion/ticker_extractor.py`

```python
import re
from typing import Set, List

# Ported from signalscope's ticker-utils.ts
TICKER_REGEX = r'\b([A-Z]{1,5})\b'

BLACKLIST = {
    # English words
    'A', 'I', 'AM', 'AN', 'AS', 'AT', 'BE', 'BY', 'DO', 'GO', 'IF', 'IN',
    'IS', 'IT', 'ME', 'MY', 'NO', 'OF', 'OK', 'ON', 'OR', 'SO', 'TO', 'UP',
    # Financial terms
    'CEO', 'IPO', 'ETF', 'SEC', 'FBI', 'FDA', 'IMO', 'YOLO', 'FOMO',
    'EPS', 'GDP', 'CPI', 'ATH', 'ATL', 'OTC', 'NYSE', 'NASDAQ',
    # Common ETFs
    'SPY', 'QQQ', 'IWM', 'DIA', 'MDY', 'VTI', 'VOO', 'VEA', 'VWO',
    'TQQQ', 'SQQQ', 'SPXL', 'SPXS', 'UVXY', 'SVXY',
    # Add more from signalscope's full blacklist...
}

MEGA_CAPS = {
    'AAPL', 'MSFT', 'GOOG', 'GOOGL', 'AMZN', 'META', 'TSLA', 'NVDA',
    # Add more...
}

def extract_tickers(text: str) -> List[str]:
    """Extract stock tickers from unstructured text."""
    matches = re.findall(TICKER_REGEX, text)
    return list(set([
        t for t in matches 
        if t not in BLACKLIST 
        and t not in MEGA_CAPS 
        and len(t) >= 2
    ]))

def extract_tickers_from_article(article_text: str, title: str = "") -> List[str]:
    """Extract tickers from a news article."""
    full_text = f"{title} {article_text}"
    return extract_tickers(full_text)
```

#### 1.2 Integrate with Existing News Ingestion

**Modify:** `backend/services/data_ingestion/parser.py` or `scraper.py`

```python
from .ticker_extractor import extract_tickers_from_article

def parse_article(article_data):
    # Existing parsing logic...
    
    # NEW: Extract tickers automatically
    discovered_tickers = extract_tickers_from_article(
        article_data['content'], 
        article_data.get('title', '')
    )
    
    # Add to article metadata
    article_data['discovered_tickers'] = discovered_tickers
    
    return article_data
```

#### 1.3 Store Discovered Tickers

**Modify:** `backend/database/models.py`

Add new table or column:
```python
class ArticleTicker(Base):
    __tablename__ = 'article_tickers'
    
    id = Column(Integer, primary_key=True)
    article_id = Column(Integer, ForeignKey('news_articles.id'))
    ticker = Column(String(10))
    confidence = Column(Float)  # How confident we are this is a real ticker
    created_at = Column(DateTime, default=datetime.utcnow)
```

---

### Phase 2: Add Signal Sources (Week 2-3)

**Goal:** Port SignalScope's signal sources to automatically discover tickers from more sources

#### 2.1 Port Reddit Scraper (HIGHEST PRIORITY)

**New File:** `backend/services/data_ingestion/reddit_client.py`

Ported from: `signalscope/src/lib/harvester/sources/reddit.ts`

Key features to port:
- 17 subreddits with different sort types (new, rising, hot)
- Velocity scoring (rising=3, hot=2, new<3h=2)
- Flair-based weighting (DD=1.5x, News=1.4x)
- Comment extraction for engagement signals

#### 2.2 Port SEC Insider Trading Fetcher

**New File:** `backend/services/data_ingestion/sec_insider_client.py`

Ported from: `signalscope/src/lib/harvester/sources/sec-insider.ts`

Key features:
- Form 4 insider trades
- Filter by transaction type (open market purchases = bullish)
- Minimum value threshold ($10k+)

#### 2.3 Create Signal Aggregation Service

**New File:** `backend/services/analysis/signal_aggregator.py`

Ported from: `signalscope/src/lib/harvester/index.ts` - `aggregateSignals()`

```python
def aggregate_signals(raw_signals: List[RawSignal]) -> List[AggregatedSymbol]:
    """Group signals by ticker and calculate metrics."""
    by_symbol = {}
    
    for signal in raw_signals:
        if signal.symbol not in by_symbol:
            by_symbol[signal.symbol] = []
        by_symbol[signal.symbol].append(signal)
    
    aggregated = []
    for symbol, signals in by_symbol.items():
        aggregated.append({
            'symbol': symbol,
            'signals': signals,
            'source_count': len(set(s.source for s in signals)),
            'total_upvotes': sum(s.upvotes or 0 for s in signals),
            'avg_velocity': calculate_velocity(signals),
            'momentum': calculate_momentum(signals),
        })
    
    # Sort by source count (most corroborated first)
    aggregated.sort(key=lambda x: (-x['source_count'], -len(x['signals'])))
    return aggregated
```

---

### Phase 3: Scoring & Filtering (Week 4)

**Goal:** Rank discovered tickers and filter out bad ones

#### 3.1 Port Advanced Scoring

**New File:** `backend/services/scoring/advanced_scorer.py`

Ported from: `signalscope/src/lib/harvester/scoring.ts`

Key features:
- AI batch scoring with hard-rule overrides
- Social-only signals capped at 50/100
- Catalyst weighting (insider > options > social)
- Velocity and momentum adjustments

#### 3.2 Port Pump-and-Dump Detection

**New File:** `backend/services/risk/pump_dump_detector.py`

Ported from: `signalscope/src/lib/harvester/pnd-filter.ts`

Key features:
- 11-flag detection system
- ML-validated thresholds
- AI-assisted borderline cases

#### 3.3 Create Opportunity Ranking

**New File:** `backend/services/analysis/opportunity_ranker.py`

```python
def rank_opportunities(aggregated_signals, scores, pnd_results):
    """Rank discovered tickers by opportunity quality."""
    opportunities = []
    
    for agg in aggregated_signals:
        symbol = agg['symbol']
        
        # Skip pump-and-dump candidates
        if pnd_results.get(symbol, {}).get('flagged'):
            continue
        
        score = scores.get(symbol, {}).get('score', 0)
        
        opportunities.append({
            'symbol': symbol,
            'score': score,
            'source_count': agg['source_count'],
            'velocity': agg['avg_velocity'],
            'reason': generate_reason(agg, scores[symbol]),
        })
    
    # Sort by score (highest first)
    opportunities.sort(key=lambda x: -x['score'])
    return opportunities
```

---

### Phase 4: Change User Workflow (Week 5)

**Goal:** Transition from "manual ticker selection" to "automatic discovery with override"

#### 4.1 New API Endpoint: `/api/v1/discover`

**New File:** `backend/routers/discovery.py`

```python
@router.get("/api/v1/discover")
async def discover_opportunities(
    min_score: int = 50,
    max_results: int = 20,
    sources: List[str] = Query(default=["reddit", "sec_insider"])
):
    """Automatically discover trading opportunities."""
    
    # 1. Fetch signals from enabled sources
    signals = await fetch_signals_from_sources(sources)
    
    # 2. Aggregate by ticker
    aggregated = aggregate_signals(signals)
    
    # 3. Score all discovered tickers
    scores = await score_symbol_batch(aggregated)
    
    # 4. Filter pump-and-dump
    pnd_results = check_pnd_flags_batch(aggregated)
    
    # 5. Rank and return top opportunities
    opportunities = rank_opportunities(aggregated, scores, pnd_results)
    
    # 6. Filter by minimum score
    filtered = [o for o in opportunities if o['score'] >= min_score]
    
    return {
        'opportunities': filtered[:max_results],
        'total_discovered': len(opportunities),
        'sources_used': sources,
    }
```

#### 4.2 Modify Existing Analysis Endpoint

**Modify:** `backend/routers/analysis.py`

Add automatic discovery option:
```python
@router.post("/api/v1/analyze/stream")
async def analyze_stream(request: AnalysisRequest):
    symbols = request.symbols
    
    # NEW: If no symbols provided, discover automatically
    if not symbols:
        discovery_results = await discover_opportunities(
            min_score=60,
            max_results=10
        )
        symbols = [o['symbol'] for o in discovery_results['opportunities']]
        
        # Log automatic discovery
        print(f"Auto-discovered {len(symbols)} symbols: {symbols}")
    
    # Continue with existing analysis pipeline...
```

#### 4.3 Frontend: Opportunity Dashboard

**New File:** `frontend/src/app/opportunities/page.tsx`

```typescript
// Display automatically discovered opportunities
export default function OpportunitiesPage() {
  const [opportunities, setOpportunities] = useState([]);
  
  useEffect(() => {
    fetch('/api/v1/discover?min_score=60&max_results=20')
      .then(res => res.json())
      .then(data => setOpportunities(data.opportunities));
  }, []);
  
  return (
    <div>
      <h1>Discovered Opportunities</h1>
      {opportunities.map(opp => (
        <SignalCard 
          key={opp.symbol}
          symbol={opp.symbol}
          score={opp.score}
          sources={opp.source_count}
          reason={opp.reason}
        />
      ))}
    </div>
  );
}
```

---

## Benefits of This Approach

### For Users
1. **No manual ticker selection required** - System finds opportunities automatically
2. **Discover hidden gems** - Finds tickers user wouldn't have thought of
3. **Better risk management** - P&D detection prevents bad trades
4. **Multi-source corroboration** - Only shows tickers with 2+ sources

### For the System
1. **More data** - Automatically builds a database of ticker mentions over time
2. **Better scoring** - More signals = better ML models
3. **Trend detection** - Can track which tickers are trending up/down
4. **User retention** - Users come back to see new discoveries

---

## Testing Strategy

### Unit Tests
1. Test ticker extraction regex with various inputs
2. Test signal aggregation logic
3. Test scoring algorithm with mock data

### Integration Tests
1. Test Reddit scraper with mock API responses
2. Test full discovery pipeline end-to-end
3. Test P&D detection with known good/bad tickers

### Paper Trading Validation
1. Automatically discover 20 tickers per day
2. Paper trade top 5 scoring tickers
3. Track performance vs manual selection

---

## Configuration Changes

### logic_config.json Additions
```json
{
  "auto_discovery": {
    "enabled": true,
    "min_score": 60,
    "max_daily_discoveries": 20,
    "sources": {
      "reddit": {
        "enabled": true,
        "subreddits": ["wallstreetbets", "stocks", "investing"],
        "min_posts": 3
      },
      "sec_insider": {
        "enabled": true,
        "min_value": 10000
      }
    },
    "pump_dump_detection": {
      "enabled": true,
      "threshold": 3
    }
  }
}
```

---

## Success Metrics

After implementation, measure:

1