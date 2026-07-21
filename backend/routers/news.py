"""
Unified News & Filings API endpoints.

Provides a single endpoint to fetch ALL data sources:
- SEC EDGAR filings (sec_filings table)
- RSS feed articles (scraped_articles table)
- Truth Social posts (posts table) - if available
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from database.engine import get_db
from database.models import SecFiling, ScrapedArticle, Post

router = APIRouter(tags=["news"])  # prefix added in main.py during include_router

logger = logging.getLogger(__name__)


@router.get("")
async def get_unified_news(
    symbol: Optional[str] = Query(None, description="Filter by symbol (EDGAR only)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    source: Optional[str] = Query(None, description="Filter by source: edgar, rss, truth_social"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get unified news/filings for all tracked symbols.
    
    Returns a combined list of:
    - SEC EDGAR filings (sec_filings table)
    - RSS feed articles (scraped_articles table)
    - Truth Social posts (posts table) - if available
    """
    all_items = []
    
    # Parse date filters
    date_filter = {}
    if start_date:
        try:
            date_filter["start"] = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")
    
    if end_date:
        try:
            date_filter["end"] = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD.")
    
    # Query 1: SEC EDGAR Filings
    if not source or source == "edgar":
        filings_query = db.query(SecFiling)
        
        # Apply filters
        if symbol:
            filings_query = filings_query.filter(SecFiling.symbol == symbol.upper())
        
        if date_filter.get("start"):
            filings_query = filings_query.filter(SecFiling.filing_date >= date_filter["start"])
        
        if date_filter.get("end"):
            filings_query = filings_query.filter(SecFiling.filing_date < date_filter["end"])
        
        # Convert to unified format
        for f in filings_query.all():
            all_items.append({
                "id": f"edgar_{f.id}",
                "source": "edgar",
                "source_label": "SEC EDGAR",
                "symbol": f.symbol,
                "title": f"{f.form_type} filing ({f.filing_date.strftime('%Y-%m-%d') if f.filing_date else 'N/A'})",
                "summary": f.llm_summary or "No summary available",
                "published_at": f.filing_date,
                "url": f.primary_document_url,
                "processed": f.processed,
                "details": {
                    "form_type": f.form_type,
                    "items": f.items,
                    "has_raw_text": bool(f.raw_text),
                    "has_summary": bool(f.llm_summary),
                }
            })
    
    # Query 2: RSS Feed Articles
    if not source or source == "rss":
        articles_query = db.query(ScrapedArticle).filter(
            ScrapedArticle.processed == True  # Only show processed articles
        )
        
        # Apply date filters
        if date_filter.get("start"):
            articles_query = articles_query.filter(ScrapedArticle.published_at >= date_filter["start"])
        
        if date_filter.get("end"):
            articles_query = articles_query.filter(ScrapedArticle.published_at < date_filter["end"])
        
        # Convert to unified format
        for a in articles_query.all():
            all_items.append({
                "id": f"rss_{a.id}",
                "source": "rss",
                "source_label": a.source or "RSS",
                "symbol": None,  # Articles don't have direct symbol mapping
                "title": a.title or "No title",
                "summary": a.summary or "No summary",
                "published_at": a.published_at,
                "url": a.url,
                "processed": a.processed,
                "details": {
                    "content_length": len(a.full_content) if a.full_content else 0,
                    "has_content": bool(a.full_content),
                }
            })
    
    # Query 3: Truth Social Posts (if table exists)
    if not source or source == "truth_social":
        try:
            posts_query = db.query(Post).filter(
                Post.source == "truth_social"
            )
            
            # Apply date filters
            if date_filter.get("start"):
                posts_query = posts_query.filter(Post.timestamp >= date_filter["start"])
            
            if date_filter.get("end"):
                posts_query = posts_query.filter(Post.timestamp < date_filter["end"])
            
            # Convert to unified format
            for p in posts_query.all():
                all_items.append({
                    "id": f"truth_{p.id}",
                    "source": "truth_social",
                    "source_label": "Truth Social",
                    "symbol": None,
                    "title": (p.content or "")[:100] + "..." if p.content and len(p.content) > 100 else p.content or "",
                    "summary": p.content or "No content",
                    "published_at": p.timestamp,
                    "url": None,
                    "processed": p.is_analyzed,
                    "details": {
                        "author": p.author,
                        "has_sentiment": bool(p.sentiment_analysis),
                    }
                })
        except Exception as exc:
            logger.warning(f"Truth Social posts table not available: {exc}")
    
    # Sort by published_at (newest first)
    all_items.sort(key=lambda x: x["published_at"] if x["published_at"] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    
    # Pagination
    total = len(all_items)
    paginated = all_items[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": paginated,
    }


@router.post("/cleanup")
async def cleanup_old_data(
    scraped_articles_days: int = Query(30, ge=1, le=365),
    sec_filings_days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """
    Clean up old data from database.
    
    Deletes:
    - RSS articles older than scraped_articles_days
    - EDGAR filings older than sec_filings_days
    """
    deleted_counts = {
        "scraped_articles": 0,
        "sec_filings": 0,
    }
    
    try:
        # Clean up old articles
        cutoff_articles = datetime.now(timezone.utc) - timedelta(days=scraped_articles_days)
        deleted_articles = db.query(ScrapedArticle).filter(
            ScrapedArticle.published_at < cutoff_articles
        ).delete()
        deleted_counts["scraped_articles"] = deleted_articles
        
        # Clean up old filings
        from services.data_ingestion.edgar_worker import cleanup_old_filings
        deleted_filings = cleanup_old_filings(retention_days=sec_filings_days)
        deleted_counts["sec_filings"] = deleted_filings
        
        db.commit()
        
        return {
            "status": "success",
            "message": "Cleanup completed",
            "deleted": deleted_counts,
        }
        
    except Exception as exc:
        logger.error(f"Cleanup failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(exc)}")
