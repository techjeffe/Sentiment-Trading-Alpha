"""
Trade List API Router
Manages the user's trading opportunity watchlist.
Allows adding discovered opportunities to a trade list for tracking.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from datetime import datetime
import json
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database.engine import SessionLocal
from database.models import TradingOpportunity, ScrapedArticle, SecFiling, InsiderSignal

router = APIRouter(prefix="/trade-list", tags=["trade-list"])


# Human-readable metadata for each signal source type. Used by the
# "Sources" detail modal so users understand what contributed to a score.
SOURCE_METADATA = {
    "NEWS": {
        "label": "News Articles",
        "description": "Scraped news articles (RSS feeds) that mention this symbol.",
    },
    "SEC_INSIDER": {
        "label": "SEC Insider Trading",
        "description": "Material open-market stock purchases by C-suite executives and directors (Form 4), sourced from OpenInsider.",
    },
    "SEC_FILING": {
        "label": "SEC Filings",
        "description": "Official SEC EDGAR filings (10-K, 10-Q, 8-K, etc.).",
    },
    "REDDIT": {
        "label": "Reddit",
        "description": "Posts from financial subreddits (e.g., r/wallstreetbets).",
    },
    "TWITTER": {
        "label": "Twitter / X",
        "description": "Posts from Twitter / X.",
    },
    "STOCKTWITS": {
        "label": "StockTwits",
        "description": "Posts from StockTwits.",
    },
    "OPTIONS_FLOW": {
        "label": "Options Flow",
        "description": "Unusual options activity (net premium, call/put ratio).",
    },
    "VOLUME_SPIKE": {
        "label": "Volume Spike",
        "description": "Abnormal trading volume relative to the recent average.",
    },
    "CONGRESS": {
        "label": "Congressional Trading",
        "description": "Disclosed trades by members of Congress.",
    },
    "POLYMARKET": {
        "label": "Polymarket",
        "description": "Prediction-market odds.",
    },
}


def _source_meta(name: str) -> dict:
    """Build the metadata dict (label/description/weight) for a source name."""
    from services.analysis.signal_aggregator import SOURCE_WEIGHTS
    meta = SOURCE_METADATA.get(name, {
        "label": name.replace("_", " ").title(),
        "description": "Signal source.",
    })
    return {
        "name": name,
        "label": meta["label"],
        "description": meta["description"],
        "weight": SOURCE_WEIGHTS.get(name, 1),
    }


def _coerce_sources(raw) -> List[str]:
    """Defensively coerce the stored `sources` JSON field into a list of strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw if s]
    return []


class AddToTradeListRequest(BaseModel):
    """Request model for adding an opportunity to the trade list."""
    symbol: str
    score: int
    sentiment: str
    reasoning: str
    source_count: int
    signal_count: int
    is_pump_and_dump: bool = False
    flags: List[str] = []
    sources: List[str] = []


class TradingOpportunityResponse(BaseModel):
    """Response model for trading opportunities."""
    id: int
    symbol: str
    score: int
    sentiment: str
    reasoning: str
    source_count: int
    signal_count: int
    is_pump_and_dump: bool
    flags: List[str]
    sources: List[str]
    added_at: datetime
    status: str
    notes: Optional[str] = None
    
    class Config:
        from_attributes = True


@router.post("/add", response_model=TradingOpportunityResponse)
async def add_to_trade_list(request: AddToTradeListRequest):
    """
    Add a discovered opportunity to the trade list.
    
    This allows users to save interesting opportunities for later analysis
    and potential trading.
    """
    db = SessionLocal()
    try:
        # Check if already in trade list
        existing = db.query(TradingOpportunity).filter(
            TradingOpportunity.symbol == request.symbol,
            TradingOpportunity.status == "watchlist"
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"{request.symbol} is already in your trade list"
            )
        
        # Create new trading opportunity
        opportunity = TradingOpportunity(
            symbol=request.symbol,
            score=request.score,
            sentiment=request.sentiment,
            reasoning=request.reasoning,
            source_count=request.source_count,
            signal_count=request.signal_count,
            is_pump_and_dump=request.is_pump_and_dump,
            flags=request.flags,
            sources=request.sources,
            status="watchlist"
        )
        
        db.add(opportunity)
        db.commit()
        db.refresh(opportunity)
        
        return opportunity
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add to trade list: {str(e)}"
        )
    finally:
        db.close()


@router.get("/", response_model=List[TradingOpportunityResponse])
async def get_trade_list(
    status: str = Query(default="watchlist", description="Filter by status"),
    min_score: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=100)
):
    """
    Get the current trade list.
    
    Returns opportunities that have been added to the watchlist.
    """
    db = SessionLocal()
    try:
        query = db.query(TradingOpportunity).filter(
            TradingOpportunity.status == status,
            TradingOpportunity.score >= min_score
        ).order_by(
            TradingOpportunity.score.desc(),
            TradingOpportunity.added_at.desc()
        ).limit(limit)
        
        return query.all()
        
    finally:
        db.close()


@router.get("/{opportunity_id}/sources")
async def get_opportunity_sources(opportunity_id: int, db=None):
    """
    Get a detailed per-source breakdown for a trading opportunity.

    For each source that contributed to this opportunity, returns:
    - A human-readable label, description, and scoring weight
    - The actual items (articles / filings) that contributed, reconstructed
      from the database where possible

    This powers the "Sources" detail modal on the trade-list page.

    db is optional and injected for testability; when omitted a session is
    created for the duration of the call.
    """
    own_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        opportunity = db.query(TradingOpportunity).filter(
            TradingOpportunity.id == opportunity_id
        ).first()

        if not opportunity:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        symbol = (opportunity.symbol or "").upper()
        source_names = _coerce_sources(opportunity.sources)

        # Reconstruct NEWS items: articles that mention the symbol and were
        # present when this opportunity was discovered.  Mirroring the discovery
        # pipeline (newest 100 at discovery time), we anchor the window to
        # opportunity.added_at instead of the *current* newest rows — otherwise a
        # NEWS-backed entry opened more than 100 articles ago would report 0
        # evidence for a score that still says NEWS contributed.
        news_items: List[dict] = []
        if "NEWS" in source_names:
            from services.data_ingestion.ticker_extractor import extract_tickers_from_article
            # >= allows for the server_default=func.now() timestamp jitter and a
            # row that lands in the same instant the opportunity was created.
            cutoff = opportunity.added_at
            q = db.query(ScrapedArticle)
            if cutoff is not None:
                q = q.filter(ScrapedArticle.discovered_at <= cutoff)
            articles = q.order_by(
                ScrapedArticle.discovered_at.desc()
            ).limit(100).all()
            for a in articles:
                title = a.title or ""
                content = a.full_content or a.summary or ""
                tickers = extract_tickers_from_article(title, content)
                if symbol in tickers:
                    news_items.append({
                        "title": title,
                        "url": a.url,
                        "published_at": a.published_at.isoformat() if a.published_at else None,
                        "source_label": a.source or "RSS",
                        "summary": (a.summary or "")[:300],
                    })

        # Reconstruct SEC items: recent SEC filings for the symbol.
        sec_items: List[dict] = []
        if "SEC_FILING" in source_names:
            filings = db.query(SecFiling).filter(
                SecFiling.symbol == symbol
            ).order_by(SecFiling.filing_date.desc()).limit(20).all()
            for f in filings:
                sec_items.append({
                    "title": f"{f.form_type} filing ({f.filing_date.strftime('%Y-%m-%d') if f.filing_date else 'N/A'})",
                    "url": f.primary_document_url,
                    "published_at": f.filing_date.isoformat() if f.filing_date else None,
                    "source_label": "SEC EDGAR",
                    "summary": (f.llm_summary or "")[:300],
                    "form_type": f.form_type,
                })

        # Reconstruct SEC_INSIDER items: persisted insider (OpenInsider) buys.
        insider_items: List[dict] = []
        if "SEC_INSIDER" in source_names:
            signals = db.query(InsiderSignal).filter(
                InsiderSignal.symbol == symbol
            ).order_by(InsiderSignal.trade_date.desc()).limit(20).all()
            for s in signals:
                value_str = f"${s.value:,.0f}" if s.value is not None else "n/a"
                qty_str = f"{s.qty:,}" if s.qty is not None else "n/a"
                price_str = f"${s.price:.2f}" if s.price is not None else "n/a"
                summary = f"{s.insider_title or 'Insider'} purchased {qty_str} shares at {price_str} ({value_str} total)"
                if s.trade_date:
                    summary += f" on {s.trade_date}"
                insider_items.append({
                    "title": f"Insider Purchase: {s.insider_name} ({s.insider_title})",
                    "url": s.source_link or s.url,
                    "published_at": s.filing_date or s.trade_date,
                    "source_label": "SEC Insider (OpenInsider)",
                    "summary": summary,
                    "insider_name": s.insider_name,
                    "value": s.value,
                })

        sources_out: List[dict] = []
        for name in source_names:
            meta = _source_meta(name)
            if name == "NEWS":
                items = news_items
            elif name == "SEC_INSIDER":
                # Stored insider buys (fetch from OpenInsider at discovery time).
                items = insider_items
            elif name == "SEC_FILING":
                items = sec_items
            else:
                items = []
            meta["items_found"] = len(items)
            meta["items"] = items[:10]
            sources_out.append(meta)

        return {
            "symbol": symbol,
            "score": opportunity.score,
            "source_count": opportunity.source_count,
            "signal_count": opportunity.signal_count,
            "sources": sources_out,
        }

    finally:
        if own_session:
            db.close()


@router.delete("/{opportunity_id}")
async def remove_from_trade_list(opportunity_id: int):
    """
    Remove an opportunity from the trade list.
    """
    db = SessionLocal()
    try:
        opportunity = db.query(TradingOpportunity).filter(
            TradingOpportunity.id == opportunity_id
        ).first()
        
        if not opportunity:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found"
            )
        
        db.delete(opportunity)
        db.commit()
        
        return {"message": "Opportunity removed from trade list"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove from trade list: {str(e)}"
        )
    finally:
        db.close()


@router.put("/{opportunity_id}/status")
async def update_status(
    opportunity_id: int,
    body: dict = Body(...)
):
    """
    Update the status of a trading opportunity.
    
    Status can be: watchlist, trading, closed
    When status is changed to 'trading', the symbol is also added to tracked_symbols.
    """
    status = body.get("status")
    if not status:
        raise HTTPException(
            status_code=400,
            detail="Status field is required in request body"
        )
    
    if status not in ["watchlist", "trading", "closed"]:
        raise HTTPException(
            status_code=400,
            detail="Status must be: watchlist, trading, or closed"
        )
    
    db = SessionLocal()
    try:
        opportunity = db.query(TradingOpportunity).filter(
            TradingOpportunity.id == opportunity_id
        ).first()
        
        if not opportunity:
            raise HTTPException(
                status_code=404,
                detail=f"Opportunity with ID {opportunity_id} not found"
            )
        
        old_status = opportunity.status
        
        # Check if changing to 'trading' and symbol already has a 'trading' entry
        if status == "trading" and old_status != "trading":
            existing_trading = db.query(TradingOpportunity).filter(
                TradingOpportunity.symbol == opportunity.symbol,
                TradingOpportunity.status == "trading",
                TradingOpportunity.id != opportunity_id
            ).first()
            
            if existing_trading:
                # Update the existing trading entry instead of creating a duplicate
                existing_trading.score = opportunity.score
                existing_trading.sentiment = opportunity.sentiment
                existing_trading.reasoning = opportunity.reasoning
                existing_trading.source_count = opportunity.source_count
                existing_trading.signal_count = opportunity.signal_count
                existing_trading.is_pump_and_dump = opportunity.is_pump_and_dump
                existing_trading.flags = opportunity.flags
                existing_trading.sources = opportunity.sources
                
                # Delete the new entry since we're merging into the existing one
                db.delete(opportunity)
                db.commit()
                
                return {
                    "message": f"Updated existing trading entry for {opportunity.symbol} (merged from duplicate)",
                    "symbol_added_to_tracked": False,
                    "merged": True
                }
        
        opportunity.status = status
        
        # If changing to 'trading', add symbol to tracked_symbols
        if status == "trading" and old_status != "trading":
            try:
                from services.app_config import get_or_create_app_config
                config = get_or_create_app_config(db)
                tracked_symbols = list(config.tracked_symbols or ["USO", "IBIT", "QQQ", "SPY"])
                custom_symbols = list(config.custom_symbols or [])
                
                symbol_added = False
                if opportunity.symbol not in tracked_symbols:
                    tracked_symbols.append(opportunity.symbol)
                    config.tracked_symbols = tracked_symbols
                    symbol_added = True
                
                if opportunity.symbol not in custom_symbols:
                    custom_symbols.append(opportunity.symbol)
                    config.custom_symbols = custom_symbols
                    symbol_added = True
                
                if symbol_added:
                    db.commit()
                    print(f"Added {opportunity.symbol} to tracked_symbols and custom_symbols")
                    
                    # Pull price history for new symbol
                    try:
                        from services.data_ingestion.yfinance_client import PriceClient
                        client = PriceClient()
                        client.pull_and_store_history(symbols=[opportunity.symbol], db=db, delay_seconds=1.0)
                        print(f"Pulled price history for {opportunity.symbol}")
                    except Exception as e:
                        print(f"Price history pull error for {opportunity.symbol}: {e}")
                    
                    # Generate symbol keywords/proxy terms if model is configured
                    model_name = str(getattr(config, "extraction_model", "") or "").strip()
                    if model_name:
                        try:
                            from services.symbol_proxy_terms import ensure_symbol_proxy_terms_fresh
                            await ensure_symbol_proxy_terms_fresh(
                                db=db,
                                config=config,
                                symbols=[opportunity.symbol],
                                model_name=model_name,
                                force=True,
                            )
                            print(f"Generated proxy terms for {opportunity.symbol}")
                        except Exception as e:
                            print(f"Symbol keyword generation error for {opportunity.symbol}: {e}")
                            import traceback
                            print(traceback.format_exc())
                    
                    # Record audit event
                    try:
                        from services.audit_log import record_audit_event
                        record_audit_event(
                            action="add_tracked_symbol_from_trade_list",
                            resource="trade_list",
                            detail=f"Added {opportunity.symbol} to tracked symbols from trade list",
                            event_metadata={"symbol": opportunity.symbol, "opportunity_id": opportunity_id},
                        )
                    except Exception as e:
                        print(f"Audit log error: {e}")
            except Exception as e:
                print(f"Warning: Could not add {opportunity.symbol} to tracked symbols: {e}")
                import traceback
                print(traceback.format_exc())
        
        db.commit()
        
        return {
            "message": f"Status updated to {status}",
            "symbol_added_to_tracked": status == "trading" and old_status != "trading"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback
        print(f"Error updating trade list status: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update status: {str(e)}"
        )
    finally:
        db.close()


@router.get("/summary")
async def get_trade_list_summary():
    """
    Get a summary of the trade list.
    
    Returns counts by status and average scores.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import func
        
        # Get counts by status
        status_counts = db.query(
            TradingOpportunity.status,
            func.count(TradingOpportunity.id).label('count')
        ).group_by(TradingOpportunity.status).all()
        
        # Get average score
        avg_score = db.query(
            func.avg(TradingOpportunity.score)
        ).filter(
            TradingOpportunity.status == "watchlist"
        ).scalar()
        
        # Get top opportunities
        top_opportunities = db.query(TradingOpportunity).filter(
            TradingOpportunity.status == "watchlist"
        ).order_by(
            TradingOpportunity.score.desc()
        ).limit(5).all()
        
        return {
            "status_counts": {row.status: row.count for row in status_counts},
            "average_score": round(avg_score or 0, 2),
            "total_opportunities": sum(row.count for row in status_counts),
            "top_opportunities": [
                {
                    "symbol": opp.symbol,
                    "score": opp.score,
                    "sentiment": opp.sentiment
                }
                for opp in top_opportunities
            ]
        }
        
    finally:
        db.close()
