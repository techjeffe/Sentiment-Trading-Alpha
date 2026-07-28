"""
Trade List API Router
Manages the user's trading opportunity watchlist.
Allows adding discovered opportunities to a trade list for tracking.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from datetime import datetime
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from database.engine import SessionLocal
from database.models import TradingOpportunity

router = APIRouter(prefix="/trade-list", tags=["trade-list"])


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
