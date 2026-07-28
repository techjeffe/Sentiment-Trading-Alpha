"""
Discovery API Router
Exposes the automatic ticker discovery system via API.
Implements /api/v1/discover endpoint.
"""

import sys
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# Add backend to path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services.data_ingestion.ticker_extractor import extract_tickers_from_article
from services.data_ingestion.sec_insider_client import fetch_sec_insider_signals
from services.analysis.signal_aggregator import aggregate_signals
from services.risk.pump_dump_detector import check_pnd_flags
from services.scoring.advanced_scorer import AggregatedSignal
from services.data_ingestion.reddit_client import RawSignal


router = APIRouter(prefix="/discover", tags=["discovery"])


class DiscoveredOpportunity(BaseModel):
    """Discovered trading opportunity."""
    symbol: str
    score: int
    sentiment: str
    reasoning: str
    source_count: int
    signal_count: int
    is_pump_and_dump: bool
    flags: List[str]
    sources: List[str]


class DiscoveryResponse(BaseModel):
    """Response from discovery endpoint."""
    total_articles_processed: int
    tickers_discovered: int
    opportunities_found: int
    opportunities: List[DiscoveredOpportunity]
    execution_time_seconds: float


@router.get("/", response_model=DiscoveryResponse)
async def discover_opportunities(
    min_score: int = Query(default=30, ge=0, le=100, description="Minimum score to include"),
    max_results: int = Query(default=20, ge=1, le=100, description="Maximum number of results"),
    include_pnd: bool = Query(default=False, description="Include pump-and-dump flagged tickers"),
    auto_add: bool = Query(default=True, description="Automatically add opportunities to trade list")
):
    """
    Discover trading opportunities automatically.
    
    This endpoint runs the full pipeline:
    1. Fetches recent news articles
    2. Extracts tickers automatically (Phase 1)
    3. Fetches SEC insider signals (Phase 2)
    4. Aggregates signals by ticker
    5. Scores opportunities (Phase 3)
    6. Filters pump-and-dump schemes
    7. Returns ranked opportunities
    
    Returns:
        DiscoveryResponse with ranked opportunities
    """
    import time
    start_time = time.time()
    
    try:
        # Import database here to avoid circular imports
        from database.engine import SessionLocal
        from database.models import ScrapedArticle
        
        db = SessionLocal()
        
        try:
            # Step 1: Fetch recent articles
            articles = db.query(ScrapedArticle).order_by(
                ScrapedArticle.discovered_at.desc()
            ).limit(100).all()
            
            if not articles:
                return DiscoveryResponse(
                    total_articles_processed=0,
                    tickers_discovered=0,
                    opportunities_found=0,
                    opportunities=[],
                    execution_time_seconds=0
                )
            
            # Step 2: Extract tickers from articles (Phase 1)
            from services.data_ingestion.ticker_extractor import extract_tickers_from_article
            
            all_signals = []
            tickers_discovered = set()
            
            for article in articles:
                title = article.title or ""
                content = article.full_content or article.summary or ""
                
                tickers = extract_tickers_from_article(title, content)
                
                if tickers:
                    tickers_discovered.update(tickers)
                    
                    # Create RawSignal for each ticker
                    for ticker in tickers:
                        signal = RawSignal(
                            symbol=ticker,
                            source="NEWS",
                            title=title[:200],
                            body=content[:500],
                            url=article.url,
                            upvotes=1,
                            post_age=24,  # Assume 24 hours old
                            sort_type="new"
                        )
                        all_signals.append(signal)
            
            # Step 3: Fetch SEC insider signals (Phase 2)
            try:
                sec_signals = fetch_sec_insider_signals()
                all_signals.extend(sec_signals)
            except Exception as e:
                print(f"SEC insider fetch error: {e}")
            
            # Step 4: Aggregate signals (Phase 2)
            aggregated = aggregate_signals(all_signals)
            
            # Step 5: Score and rank (Phase 3)
            opportunities = []
            
            for agg in aggregated:
                # Check P&D first
                pnd_result = check_pnd_flags(agg)
                
                if pnd_result.flagged and not include_pnd:
                    continue  # Skip P&D flagged tickers
                
                # Calculate heuristic score (since we need Ollama integration in the API)
                from services.scoring.advanced_scorer import default_score
                score_result = default_score(agg)
                
                # Apply min_score filter
                if score_result.score < min_score:
                    continue
                
                # Get unique sources
                sources = list(set(s.source for s in agg.signals))
                
                opportunity = DiscoveredOpportunity(
                    symbol=agg.symbol,
                    score=score_result.score,
                    sentiment=score_result.sentiment,
                    reasoning=score_result.reasoning,
                    source_count=agg.source_count,
                    signal_count=len(agg.signals),
                    is_pump_and_dump=pnd_result.flagged,
                    flags=pnd_result.flags,
                    sources=sources
                )
                opportunities.append(opportunity)
            
            # Sort by score (descending)
            opportunities.sort(key=lambda x: x.score, reverse=True)
            
            # Limit results
            opportunities = opportunities[:max_results]
            
            # Auto-add to trade list if requested
            if auto_add:
                try:
                    from database.models import TradingOpportunity
                    from datetime import datetime
                    
                    for opp in opportunities:
                        # Check if already in trade list
                        existing = db.query(TradingOpportunity).filter(
                            TradingOpportunity.symbol == opp.symbol,
                            TradingOpportunity.status == "watchlist"
                        ).first()
                        
                        if not existing:
                            new_opp = TradingOpportunity(
                                symbol=opp.symbol,
                                score=opp.score,
                                sentiment=opp.sentiment,
                                reasoning=opp.reasoning,
                                source_count=opp.source_count,
                                signal_count=opp.signal_count,
                                is_pump_and_dump=opp.is_pump_and_dump,
                                flags=opp.flags,
                                sources=opp.sources,
                                status="watchlist"
                            )
                            db.add(new_opp)
                    
                    db.commit()
                    print(f"Auto-added {len(opportunities)} opportunities to trade list")
                    
                except Exception as e:
                    print(f"Error auto-adding to trade list: {e}")
                    import traceback
                    print(traceback.format_exc())
            
            execution_time = time.time() - start_time
            
            return DiscoveryResponse(
                total_articles_processed=len(articles),
                tickers_discovered=len(tickers_discovered),
                opportunities_found=len(opportunities),
                opportunities=opportunities,
                execution_time_seconds=round(execution_time, 2)
            )
            
        finally:
            db.close()
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Discovery pipeline error: {str(e)}"
        )


@router.get("/test")
async def test_discovery():
    """Test endpoint to verify discovery system is working."""
    return {
        "status": "ok",
        "message": "Discovery API is working",
        "phases_complete": [1, 2, 3],
        "next_step": "Call /api/v1/discover to get real opportunities"
    }
