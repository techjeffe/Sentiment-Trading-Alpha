"""
SEC EDGAR filings API routes.

Provides endpoints to:
- List stored filings
- Trigger manual poll cycle
- Process unprocessed filings (fetch text, LLM summary)
- Configure EDGAR settings
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.engine import get_db
from database.models import SecFiling
from config.logic_loader import LOGIC
from services.app_config import get_or_create_app_config

router = APIRouter(tags=["edgar"])  # prefix added in main.py during include_router

logger = logging.getLogger(__name__)


@router.get("/filings")
async def list_edgar_filings(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    processed: Optional[bool] = Query(None, description="Filter by processed status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List SEC EDGAR filings stored in the database.
    """
    query = db.query(SecFiling)

    if symbol:
        query = query.filter(SecFiling.symbol == symbol.upper())

    if processed is not None:
        query = query.filter(SecFiling.processed == processed)

    query = query.order_by(SecFiling.filing_date.desc())

    total = query.count()
    filings = query.limit(limit).offset(offset).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "filings": [
            {
                "id": f.id,
                "symbol": f.symbol,
                "cik": f.cik,
                "accession_number": f.accession_number,
                "form_type": f.form_type,
                "filing_date": f.filing_date.isoformat() if f.filing_date else None,
                "items": f.items,
                "processed": f.processed,
                "discovered_at": f.discovered_at.isoformat() if f.discovered_at else None,
                "processed_at": f.processed_at.isoformat() if f.processed_at else None,
            }
            for f in filings
        ],
    }


@router.post("/poll")
async def trigger_edgar_poll(db: Session = Depends(get_db)):
    """
    Manually trigger an EDGAR polling cycle.
    """
    try:
        from services.data_ingestion.edgar_worker import run_edgar_poll_cycle
        summary = run_edgar_poll_cycle()
        return {
            "status": "success",
            "message": "EDGAR poll cycle completed",
            "summary": summary,
        }
    except Exception as exc:
        logger.error(f"EDGAR poll failed: {exc}")
        raise HTTPException(status_code=500, detail=f"EDGAR poll failed: {str(exc)}")


@router.get("/config")
async def get_edgar_config(db: Session = Depends(get_db)):
    """
    Get current EDGAR filings configuration.
    """
    config = get_or_create_app_config(db)

    # Get defaults from logic_config.json
    defaults = LOGIC.get("edgar_filings", {})

    # Build config with DB overrides
    result = {
        "enabled": config.edgar_filings_enabled if config.edgar_filings_enabled is not None else defaults.get("enabled", False),
        "poll_interval_minutes": config.edgar_filings_poll_interval_minutes if config.edgar_filings_poll_interval_minutes is not None else defaults.get("poll_interval_minutes", 60),
        "tracked_form_types": config.edgar_filings_tracked_form_types if config.edgar_filings_tracked_form_types is not None else defaults.get("tracked_form_types", ["10-K", "10-Q", "8-K"]),
        "material_8k_items": config.edgar_filings_material_8k_items if config.edgar_filings_material_8k_items is not None else defaults.get("material_8k_items", ["2.02", "5.02", "7.01", "8.01"]),
        "max_filing_chars_for_llm": defaults.get("max_filing_chars_for_llm", 40000),
        "lookback_days_on_first_poll": defaults.get("lookback_days_on_first_poll", 7),
    }

    return result


@router.put("/config")
async def update_edgar_config(
    enabled: Optional[bool] = None,
    poll_interval_minutes: Optional[int] = None,
    tracked_form_types: Optional[List[str]] = None,
    material_8k_items: Optional[List[str]] = None,
    db: Session = Depends(get_db),
):
    """
    Update EDGAR filings configuration.
    """
    config = get_or_create_app_config(db)

    if enabled is not None:
        config.edgar_filings_enabled = enabled

    if poll_interval_minutes is not None:
        config.edgar_filings_poll_interval_minutes = poll_interval_minutes

    if tracked_form_types is not None:
        config.edgar_filings_tracked_form_types = tracked_form_types

    if material_8k_items is not None:
        config.edgar_filings_material_8k_items = material_8k_items

    db.commit()

    return {"status": "success", "message": "EDGAR configuration updated"}


@router.post("/process")
async def process_unprocessed_edgar_filings(
    limit: int = Query(10, ge=1, le=50, description="Max filings to process"),
    db: Session = Depends(get_db),
):
    """
    Process unprocessed EDGAR filings: fetch text and generate LLM summaries.
    """
    try:
        from services.data_ingestion.edgar_worker import process_unprocessed_filings
        summary = await process_unprocessed_filings(limit=limit)
        return {
            "status": "success",
            "message": "EDGAR filing processing completed",
            "summary": summary,
        }
    except Exception as exc:
        logger.error(f"EDGAR processing failed: {exc}")
        raise HTTPException(status_code=500, detail=f"EDGAR processing failed: {str(exc)}")
