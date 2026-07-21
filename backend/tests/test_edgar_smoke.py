"""
Quick smoke test for EDGAR filings integration.

Run with:
    cd backend && python -m tests.test_edgar_smoke
"""

import sys
from datetime import datetime

def test_edgar_client():
    """Test EDGARClient basic functionality."""
    print("=" * 60)
    print("Test 1: EDGARClient")
    print("=" * 60)
    
    from services.data_ingestion.edgar_client import EdgarClient
    
    client = EdgarClient()
    
    # Test CIK resolution
    cik = client.get_cik_for_ticker("NVDA")
    assert cik == "0001045810", f"Expected 0001045810, got {cik}"
    print(f"  [OK] CIK resolution: NVDA -> {cik}")
    
    # Test filings fetch (may return empty if no matching forms in recent history)
    filings = client.get_recent_filings(cik, form_types=["10-K", "10-Q", "8-K"], max_filings=50)
    print(f"  [OK] Filings fetch: {len(filings)} filings returned (form types filtered)")
    if len(filings) > 0:
        print(f"       First filing: {filings[0]['form']} {filings[0]['filingDate']}")
    else:
        # Try without form filter
        all_filings = client.get_recent_filings(cik, max_filings=5)
        print(f"       (No 10-K/10-Q/8-K in first 50, but got {len(all_filings)} total filings)")
        if len(all_filings) > 0:
            print(f"       Sample forms: {[f['form'] for f in all_filings[:3]]}")
    
    print("\n[PASS] EDGARClient tests passed\n")


def test_edgar_worker():
    """Test EDGAR worker poll cycle."""
    print("=" * 60)
    print("Test 2: EDGAR Worker")
    print("=" * 60)
    
    from services.data_ingestion.edgar_worker import run_edgar_poll_cycle
    
    # Run a poll cycle (will skip if disabled)
    summary = run_edgar_poll_cycle()
    
    print(f"  Enabled: {summary['enabled']}")
    print(f"  Symbols checked: {summary['symbols_checked']}")
    print(f"  Filings discovered: {summary['filings_discovered']}")
    print(f"  Filings stored: {summary['filings_stored']}")
    
    if summary['errors']:
        print(f"  Warnings: {len(summary['errors'])} (non-fatal)")
    
    print("\n[PASS] EDGAR Worker test completed\n")


def test_database():
    """Test database models and queries."""
    print("=" * 60)
    print("Test 3: Database")
    print("=" * 60)
    
    from database.engine import SessionLocal
    from database.models import SecFiling
    
    db = SessionLocal()
    try:
        # Check table exists
        total = db.query(SecFiling).count()
        print(f"  [OK] sec_filings table: {total} rows")
        
        # Check columns
        if total > 0:
            f = db.query(SecFiling).first()
            assert f.symbol, "symbol is empty"
            assert f.accession_number, "accession_number is empty"
            assert f.form_type, "form_type is empty"
            print(f"  [OK] Sample filing: {f.symbol} {f.form_type} {f.accession_number}")
        
    finally:
        db.close()
    
    print("\n[PASS] Database tests passed\n")


def test_api_imports():
    """Test API router imports."""
    print("=" * 60)
    print("Test 4: API Router")
    print("=" * 60)
    
    from routers.edgar import router
    routes = [r for r in router.routes]
    print(f"  [OK] EDGAR router: {len(routes)} endpoints")
    
    expected_paths = ["/api/v1/edgar/filings", "/api/v1/edgar/poll", "/api/v1/edgar/process", "/api/v1/edgar/config"]
    for path in expected_paths:
        found = any(path in str(r.path) for r in routes)
        assert found, f"Missing endpoint: {path}"
        print(f"  [OK] Endpoint: {path}")
    
    print("\n[PASS] API Router tests passed\n")


def test_sentiment_integration():
    """Test sentiment engine EDGAR integration."""
    print("=" * 60)
    print("Test 5: Sentiment Engine Integration")
    print("=" * 60)
    
    from services.sentiment.prompts import format_symbol_specialist_context_prompt
    
    # Test that edgar_filing_context parameter is accepted
    prompt = format_symbol_specialist_context_prompt(
        symbol="NVDA",
        text="Test news article",
        edgar_filing_context="Recent SEC filings: 10-K filed 2024-01-15 with revenue growth.",
    )
    
    assert "Recent SEC filings" in prompt, "EDGAR context not found in prompt"
    print("  [OK] EDGAR context included in specialist prompt")
    
    print("\n[PASS] Sentiment Engine Integration tests passed\n")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("EDGAR Filings Integration — Smoke Test")
    print("=" * 60 + "\n")
    
    try:
        test_edgar_client()
        test_edgar_worker()
        test_database()
        test_api_imports()
        test_sentiment_integration()
        
        print("=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n[FAILED] Assertion error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAILED] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
