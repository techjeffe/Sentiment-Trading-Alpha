"""
EDGAR filings polling worker.

Polls SEC EDGAR for new filings from tracked companies and stores them
in the sec_filings table for later LLM processing.

Follows the same patterns as the existing data ingestion worker:
- DB-driven interval (edgar_filings.poll_interval_minutes)
- Checks the analysis lock before running
- Records outcome via runtime_health for the /health endpoint
- Graceful degradation on errors (no retries, matching repo style)
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database.engine import SessionLocal
from database.models import SecFiling
from config.logic_loader import LOGIC
from services.app_config import get_or_create_app_config
from services.sentiment.engine import SentimentEngine

from .edgar_client import EdgarClient


def _get_edgar_config(key: str, default: Any = None) -> Any:
    """
    Read an edgar_filings config value, respecting DB override if set.

    Reads from logic_config.json -> LOGIC["edgar_filings"][key],
    then checks for a matching AppConfig nullable override column
    (e.g., key="enabled" -> AppConfig.edgar_filings_enabled).
    """
    # Default from logic_config.json
    value = LOGIC.get("edgar_filings", {}).get(key, default)

    # Check for DB override
    try:
        db = SessionLocal()
        config = get_or_create_app_config(db)
        override_map = {
            "enabled": config.edgar_filings_enabled,
            "poll_interval_minutes": config.edgar_filings_poll_interval_minutes,
            "tracked_form_types": config.edgar_filings_tracked_form_types,
            "material_8k_items": config.edgar_filings_material_8k_items,
        }
        db_override = override_map.get(key)
        if db_override is not None:
            value = db_override
    except Exception:
        pass
    finally:
        if 'db' in locals():
            db.close()

    return value


def _get_tracked_symbols() -> List[str]:
    """Get the list of tracked symbols from AppConfig."""
    try:
        db = SessionLocal()
        config = get_or_create_app_config(db)
        # Combine built-in and custom symbols
        built_ins = config.tracked_symbols or []
        customs = config.custom_symbols or []
        return list(set(built_ins + customs))
    except Exception as exc:
        print(f"[edgar_worker] Failed to get tracked symbols: {exc}")
        return []
    finally:
        if 'db' in locals():
            db.close()


def _resolve_cik_for_symbol(client: EdgarClient, symbol: str) -> Optional[str]:
    """
    Resolve CIK for a symbol, using AppConfig.symbol_edgar_ciks cache.

    Returns the CIK (10-digit zero-padded) or None if not found.
    Updates the cache in AppConfig if a new CIK is resolved.
    """
    try:
        db = SessionLocal()
        config = get_or_create_app_config(db)

        # Check cache first
        ciks_cache = config.symbol_edgar_ciks or {}
        if symbol in ciks_cache:
            return ciks_cache[symbol]

        # Resolve via EDGAR API
        cik = client.get_cik_for_ticker(symbol)
        if cik:
            # Update cache
            ciks_cache[symbol] = cik
            config.symbol_edgar_ciks = ciks_cache
            db.commit()
            print(f"[edgar_worker] Resolved CIK for {symbol}: {cik}")
            return cik
        else:
            print(f"[edgar_worker] Could not resolve CIK for {symbol}")
            return None

    except Exception as exc:
        print(f"[edgar_worker] Error resolving CIK for {symbol}: {exc}")
        return None
    finally:
        if 'db' in locals():
            db.close()


def _store_filing(filing_data: Dict[str, Any]) -> bool:
    """
    Store a filing in the sec_filings table if it doesn't already exist.

    Returns True if the filing was stored, False if it already exists or on error.
    """
    try:
        db = SessionLocal()

        # Check if already exists (by accession_number)
        existing = db.query(SecFiling).filter(
            SecFiling.accession_number == filing_data["accessionNumber"]
        ).first()

        if existing:
            return False

        # Build primary document URL
        cik = filing_data["cik"]
        accn = filing_data["accessionNumber"]
        primary_doc = filing_data["primaryDocument"]
        primary_doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{primary_doc}"

        # Parse filing date
        filing_date = None
        if filing_data.get("filingDate"):
            try:
                filing_date = datetime.fromisoformat(filing_data["filingDate"])
            except ValueError:
                # Try parsing date-only format
                filing_date = datetime.strptime(filing_data["filingDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Parse report date
        report_date = None
        if filing_data.get("reportDate"):
            try:
                report_date = datetime.fromisoformat(filing_data["reportDate"])
            except ValueError:
                report_date = datetime.strptime(filing_data["reportDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Create new SecFiling record
        filing = SecFiling(
            symbol=filing_data["symbol"],
            cik=cik,
            accession_number=filing_data["accessionNumber"],
            form_type=filing_data["form"],
            filing_date=filing_date,
            report_date=report_date,
            items=filing_data.get("items", ""),
            primary_document_url=primary_doc_url,
            processed=False,
        )
        db.add(filing)
        db.commit()
        print(f"[edgar_worker] Stored new filing: {filing_data['symbol']} {filing_data['form']} {filing_data['accessionNumber']}")
        return True

    except Exception as exc:
        print(f"[edgar_worker] Error storing filing {filing_data.get('accessionNumber')}: {exc}")
        if 'db' in locals():
            db.rollback()
        return False
    finally:
        if 'db' in locals():
            db.close()


def run_edgar_poll_cycle() -> Dict[str, Any]:
    """
    Run one EDGAR polling cycle.

    Returns a summary dict with counts of filings discovered/processed.
    """
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "symbols_checked": 0,
        "filings_discovered": 0,
        "filings_stored": 0,
        "errors": [],
    }

    # Check if enabled
    enabled = _get_edgar_config("enabled", False)
    summary["enabled"] = enabled
    if not enabled:
        print("[edgar_worker] EDGAR filings integration is disabled.")
        return summary

    # Get tracked symbols
    symbols = _get_tracked_symbols()
    if not symbols:
        print("[edgar_worker] No tracked symbols configured.")
        return summary

    # Initialize EDGAR client
    user_agent = os.getenv("EDGAR_USER_AGENT", "Sentiment Trading Alpha admin@example.com")
    client = EdgarClient(user_agent=user_agent)

    # Get config values
    tracked_form_types = _get_edgar_config("tracked_form_types", ["10-K", "10-Q", "8-K"])
    material_8k_items = _get_edgar_config("material_8k_items", ["2.02", "5.02", "7.01", "8.01"])

    # Poll each symbol
    for symbol in symbols:
        try:
            # Skip ETFs (they don't file 10-K/10-Q/8-K like operating companies)
            # Basic check: if it's in the built-in list and not a custom equity, skip
            if symbol in {"USO", "IBIT", "QQQ", "SPY"}:
                print(f"[edgar_worker] Skipping ETF symbol {symbol} (no operating company filings)")
                continue

            summary["symbols_checked"] += 1

            # Resolve CIK
            cik = _resolve_cik_for_symbol(client, symbol)
            if not cik:
                continue

            # Fetch recent filings
            filings = client.get_recent_filings(
                cik=cik,
                form_types=tracked_form_types,
                max_filings=50
            )

            # Filter for material 8-Ks (if 8-K is in tracked forms)
            if "8-K" in tracked_form_types:
                filings = [
                    f for f in filings
                    if f["form"] != "8-K" or client.is_material_8k(f.get("items", ""), material_8k_items)
                ]

            # If no filings discovered and this is the first run (table is empty), fetch at least one
            if not filings:
                try:
                    db = SessionLocal()
                    total_existing = db.query(SecFiling).filter(SecFiling.symbol == symbol).count()
                finally:
                    db.close()
                
                if total_existing == 0:
                    # First ever run for this symbol - fetch without form type filter
                    print(f"[edgar_worker] First run for {symbol}, fetching latest filing...")
                    filings = client.get_recent_filings(
                        cik=cik,
                        form_types=None,  # No filter - get any recent filing
                        max_filings=1
                    )

            summary["filings_discovered"] += len(filings)

            # Store new filings
            for filing in filings:
                filing["symbol"] = symbol  # Add symbol to filing data
                if _store_filing(filing):
                    summary["filings_stored"] += 1

            # Rate limiting: sleep briefly between symbols
            time.sleep(0.5)

        except Exception as exc:
            error_msg = f"Error polling {symbol}: {exc}"
            print(f"[edgar_worker] {error_msg}")
            summary["errors"].append(error_msg)

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    print(f"[edgar_worker] Poll cycle complete: {summary['filings_stored']} new filings stored from {summary['symbols_checked']} symbols")
    return summary


async def run_edgar_poll_cycle_async() -> Dict[str, Any]:
    """Async wrapper for run_edgar_poll_cycle."""
    return await asyncio.to_thread(run_edgar_poll_cycle)


def fetch_filing_text_for_db(filing: SecFiling) -> Optional[str]:
    """
    Fetch the raw text for a SecFiling and store it in the database.
    
    Returns the extracted text, or None if extraction failed.
    """
    try:
        # Initialize EDGAR client
        user_agent = os.getenv("EDGAR_USER_AGENT", "Sentiment Trading Alpha admin@example.com")
        client = EdgarClient(user_agent=user_agent)
        
        # Fetch the filing text
        raw_text = client.fetch_filing_text(
            cik=filing.cik,
            accession_number=filing.accession_number,
            primary_document=filing.primary_document_url.split("/")[-1]
        )
        
        if raw_text:
            # Store in database
            db = SessionLocal()
            try:
                db_filing = db.query(SecFiling).filter(SecFiling.id == filing.id).first()
                if db_filing:
                    db_filing.raw_text = raw_text
                    db.commit()
                    print(f"[edgar_worker] Fetched and stored text for {filing.accession_number} ({len(raw_text)} chars)")
                    return raw_text
            finally:
                db.close()
        else:
            print(f"[edgar_worker] Failed to extract text for {filing.accession_number}")
            return None
            
    except Exception as exc:
        print(f"[edgar_worker] Error fetching text for {filing.accession_number}: {exc}")
        return None


async def summarize_filing_with_llm(filing: SecFiling) -> Optional[str]:
    """
    Use the LLM to summarize a filing's raw text.
    
    Returns the LLM-generated summary, or None if summarization failed.
    """
    if not filing.raw_text:
        print(f"[edgar_worker] No raw text for {filing.accession_number}, fetching...")
        fetch_filing_text_for_db(filing)
        # Re-fetch from DB
        db = SessionLocal()
        try:
            filing = db.query(SecFiling).filter(SecFiling.id == filing.id).first()
        finally:
            db.close()
        
    if not filing or not filing.raw_text:
        print(f"[edgar_worker] Still no raw text for {filing.accession_number}, skipping LLM summary")
        return None
     
    try:
        # Initialize SentimentEngine
        engine = SentimentEngine()
        
        # Truncate text to max chars for LLM
        max_chars = _get_edgar_config("max_filing_chars_for_llm", 40000)
        text_to_summarize = filing.raw_text[:max_chars] if filing.raw_text else ""
        
        # Build prompt for filing summarization
        prompt = f"""Please summarize this SEC {filing.form_type} filing for {filing.symbol}.

Focus on:
- Key financial results (for 10-K/10-Q)
- Material events or changes (for 8-K)
- Risk factors or concerns
- Forward-looking statements
- Any market-relevant information

Filing text:
{text_to_summarize}

Provide a concise 2-3 paragraph summary focusing on market-relevant information."""
        
        # Call LLM (using extraction model for cost efficiency)
        # Note: This is a simplified version - in production, use proper JSON-structured extraction
        # Use the engine's internal LLM call method
        response_data = await engine._call_ollama(
            prompt=prompt,
            model_override=engine.config.extraction_model if hasattr(engine, 'config') else None,
            force_json=False,
        )
        
        if response_data:
            # Extract text from response
            if isinstance(response_data, dict):
                summary = response_data.get('response', response_data.get('text', str(response_data)))
            else:
                summary = str(response_data)
            
            # Store in database
            db = SessionLocal()
            try:
                db_filing = db.query(SecFiling).filter(SecFiling.id == filing.id).first()
                if db_filing:
                    db_filing.llm_summary = summary
                    db_filing.processed = True
                    db_filing.processed_at = datetime.now(timezone.utc)
                    db.commit()
                    print(f"[edgar_worker] Generated LLM summary for {filing.accession_number}")
                    return summary
            finally:
                db.close()
        
        return None
        
    except Exception as exc:
        print(f"[edgar_worker] Error summarizing {filing.accession_number}: {exc}")
        return None


def cleanup_old_filings(retention_days: int = 90) -> int:
    """
    Delete sec_filings older than retention_days.
    
    Returns the number of deleted records.
    """
    try:
        db = SessionLocal()
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        
        deleted = db.query(SecFiling).filter(
            SecFiling.filing_date < cutoff
        ).delete()
        db.commit()
        
        if deleted > 0:
            print(f"[edgar_worker] Cleaned up {deleted} old filings (older than {retention_days} days)")
        
        return deleted
    except Exception as exc:
        print(f"[edgar_worker] Error cleaning up old filings: {exc}")
        return 0
    finally:
        if 'db' in locals():
            db.close()


async def process_unprocessed_filings(limit: int = 10) -> Dict[str, Any]:
    """
    Process unprocessed filings: fetch text and run LLM summarization.
    
    Returns a summary dict with counts of processed filings.
    """
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "filings_to_process": 0,
        "text_fetched": 0,
        "summaries_generated": 0,
        "errors": [],
    }
    
    try:
        db = SessionLocal()
        
        # Get unprocessed filings
        unprocessed = db.query(SecFiling).filter(
            SecFiling.processed == False
        ).limit(limit).all()
        
        summary["filings_to_process"] = len(unprocessed)
        
        for filing in unprocessed:
            try:
                # Fetch text if not already fetched
                if not filing.raw_text:
                    fetch_filing_text_for_db(filing)
                    summary["text_fetched"] += 1
                    # Re-fetch from DB to get updated raw_text
                    db.refresh(filing)
                
                # Generate LLM summary if text exists and summary doesn't
                if filing.raw_text and not filing.llm_summary:
                    await summarize_filing_with_llm(filing)
                    summary["summaries_generated"] += 1
                
                # Rate limiting
                await asyncio.sleep(1)
                
            except Exception as exc:
                error_msg = f"Error processing {filing.accession_number}: {exc}"
                print(f"[edgar_worker] {error_msg}")
                summary["errors"].append(error_msg)
        
    except Exception as exc:
        print(f"[edgar_worker] Error in process_unprocessed_filings: {exc}")
        summary["errors"].append(str(exc))
    finally:
        if 'db' in locals():
            db.close()
    
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    print(f"[edgar_worker] Processing complete: {summary['summaries_generated']} summaries generated from {summary['filings_to_process']} filings")
    return summary


def get_recent_filing_summaries_for_symbol(symbol: str, days: int = 30, max_filings: int = 5) -> str:
    """
    Get recent processed filing summaries for a symbol.
    
    Returns a formatted string with recent filing summaries, or empty string if none.
    """
    try:
        db = SessionLocal()
        
        # Calculate cutoff date
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Get recent processed filings for the symbol
        filings = db.query(SecFiling).filter(
            SecFiling.symbol == symbol.upper(),
            SecFiling.processed == True,
            SecFiling.filing_date >= cutoff,
        ).order_by(SecFiling.filing_date.desc()).limit(max_filings).all()
        
        if not filings:
            return ""
        
        # Format summaries
        lines = [f"Recent SEC filings for {symbol.upper()}:"]
        for f in filings:
            filing_date = f.filing_date.strftime("%Y-%m-%d") if f.filing_date else "unknown"
            summary = f.llm_summary or "No summary available"
            # Truncate summary to 500 chars
            if summary and len(summary) > 500:
                summary = summary[:500] + "..."
            lines.append(f"  - {f.form_type} filed {filing_date}: {summary}")
        
        return "\n".join(lines)
        
    except Exception as exc:
        print(f"[edgar_worker] Error getting filing summaries for {symbol}: {exc}")
        return ""
    finally:
        if 'db' in locals():
            db.close()


def test_edgar_worker():
    """
    Basic test function for edgar_worker.
    Run with: python -m services.data_ingestion.edgar_worker
    """
    print("Testing edgar_worker...")

    # First, enable EDGAR in the config for testing
    print("\nEnabling EDGAR filings for testing...")
    try:
        db = SessionLocal()
        config = get_or_create_app_config(db)
        config.edgar_filings_enabled = True
        config.tracked_symbols = ["NVDA"]  # Add NVDA for testing
        db.commit()
        print("[OK] EDGAR enabled, NVDA added to tracked symbols")
    except Exception as exc:
        print(f"[FAIL] Could not enable EDGAR: {exc}")
        return
    finally:
        if 'db' in locals():
            db.close()

    print(f"\n  EDGAR enabled: {_get_edgar_config('enabled')}")
    print(f"  Poll interval: {_get_edgar_config('poll_interval_minutes')} minutes")
    print(f"  Tracked form types: {_get_edgar_config('tracked_form_types')}")
    print(f"  Material 8-K items: {_get_edgar_config('material_8k_items')}")

    # Test a single poll cycle
    print("\nRunning poll cycle...")
    summary = run_edgar_poll_cycle()
    print(f"\nPoll cycle summary:")
    print(f"  Enabled: {summary['enabled']}")
    print(f"  Symbols checked: {summary['symbols_checked']}")
    print(f"  Filings discovered: {summary['filings_discovered']}")
    print(f"  Filings stored: {summary['filings_stored']}")
    if summary['errors']:
        print(f"  Errors: {len(summary['errors'])}")
        for err in summary['errors'][:3]:
            print(f"    - {err}")


if __name__ == "__main__":
    test_edgar_worker()
