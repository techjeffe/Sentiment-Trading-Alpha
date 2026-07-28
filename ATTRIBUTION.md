# Attribution

## SignalScope Integration

This project incorporates components from **[SignalScope](https://github.com/aleibovici/signalscope)** (MIT License).

**Copyright (c) 2026 SignalScope contributors**

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

### Components Ported from SignalScope

The following modules were ported from SignalScope to Sentiment Trading Alpha:

1. **Ticker Extraction** (`backend/services/data_ingestion/ticker_extractor.py`)
   - Original: `signalscope/src/lib/harvester/sources/ticker-utils.ts`
   - Automatically extracts ticker symbols from unstructured text
   - Filters out 200+ blacklisted words
   - Handles cashtag format ($AAPL)

2. **Reddit Client** (`backend/services/data_ingestion/reddit_client.py`)
   - Original: `signalscope/src/lib/harvester/sources/reddit.ts`
   - Scans 17 subreddits for stock mentions
   - Uses PRAW (Python Reddit API Wrapper)

3. **SEC Insider Client** (`backend/services/data_ingestion/sec_insider_client.py`)
   - Original: `signalscope/src/lib/harvester/sources/sec-insider.ts`
   - Fetches insider trading data from OpenInsider.com
   - Filters for C-suite/director purchases ≥ $50K

4. **Signal Aggregator** (`backend/services/analysis/signal_aggregator.py`)
   - Original: `signalscope/src/lib/harvester/index.ts`
   - Aggregates raw signals by ticker symbol
   - Calculates velocity, momentum, source diversity

5. **Pump & Dump Detector** (`backend/services/risk/pump_dump_detector.py`)
   - Original: `signalscope/src/lib/harvester/pnd-filter.ts`
   - 11-flag rule-based detection system
   - Filters out scam stocks before recommendations

6. **Advanced Scorer** (`backend/services/scoring/advanced_scorer.py`)
   - Original: `signalscope/src/lib/harvester/scoring.ts`
   - AI scoring system with Ollama integration
   - Heuristic fallback scoring

7. **Ollama Client** (`backend/services/scoring/ollama_client.py`)
   - Integrated with STA's existing Ollama setup
   - JSON response parsing

### Modifications

The original TypeScript code was ported to Python and integrated with Sentiment Trading Alpha's existing architecture. Modifications include:
- Python async/await patterns
- SQLAlchemy database integration
- FastAPI endpoint exposure
- Integration with STA's configuration system

### Acknowledgment

We thank the SignalScope contributors for their excellent work on automatic ticker discovery and signal aggregation. Their system forms the foundation of the discovery features in Sentiment Trading Alpha.

For more information about SignalScope, visit: https://github.com/aleibovici/signalscope

---

## Other Attributions

- **Ollama** - Local LLM inference engine (https://ollama.com)
- **FastAPI** - Modern Python web framework (https://fastapi.tiangolo.com)
- **Next.js** - React framework (https://nextjs.org)
- **Playwright** - Browser automation (https://playwright.dev)

