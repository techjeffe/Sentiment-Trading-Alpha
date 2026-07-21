"""
Test LLM reading of SEC EDGAR 10-K and 10-Q filings.

This test:
1. Fetches a recent 10-K and 10-Q filing for a symbol
2. Extracts the text using trafilatura
3. Sends the text to the LLM for summarization
4. Prints the LLM-generated summary

Run with:
    cd backend && python -m tests.test_edgar_llm_read
"""

import asyncio
from datetime import datetime

SYMBOL = "NVDA"
FORM_TYPES = ["10-K", "10-Q", "8-K"]  # Include 8-K for testing


async def test_llm_read_filing():
    """Test LLM reading and summarization of a single filing."""
    print("=" * 60)
    print(f"Testing LLM reading of {SYMBOL} filings")
    print("=" * 60 + "\n")

    # Step 1: Fetch recent filings
    print("Step 1: Fetching recent filings...")
    from services.data_ingestion.edgar_client import EdgarClient
    
    client = EdgarClient()
    cik = client.get_cik_for_ticker(SYMBOL)
    
    if not cik:
        print(f"[FAIL] Could not resolve CIK for {SYMBOL}")
        return
    
    print(f"[OK] Resolved CIK: {cik}")
    
    # Fetch 10-K, 10-Q, and 8-K filings
    filings = client.get_recent_filings(
        cik=cik,
        form_types=FORM_TYPES,
        max_filings=50
    )
    
    if not filings:
        print(f"[FAIL] No {FORM_TYPES} filings found for {SYMBOL}")
        # Try without form filter
        print(f"[INFO] Trying without form type filter...")
        filings = client.get_recent_filings(
            cik=cik,
            form_types=None,
            max_filings=10
        )
        if not filings:
            print(f"[FAIL] No filings found at all")
            return
        else:
            print(f"[OK] Found {len(filings)} total filings (no form filter)")
            for f in filings[:5]:
                print(f"  - {f['form']} {f['filingDate']}")
    
    print(f"[OK] Found {len(filings)} filings")
    for f in filings[:5]:
        print(f"  - {f['form']} filed {f['filingDate']} (accn: {f['accessionNumber']})")
    
    # Step 2: Fetch and extract text for the first filing
    print(f"\nStep 2: Fetching filing text...")
    test_filing = filings[0]
    accn = test_filing["accessionNumber"]
    primary_doc = test_filing["primaryDocument"]
    
    print(f"[INFO] Testing with: {test_filing['form']} {accn}")
    print(f"[INFO] Primary document: {primary_doc}")
    
    raw_text = client.fetch_filing_text(cik, accn, primary_doc)
    
    if not raw_text:
        print(f"[FAIL] Could not extract text from filing")
        return
    
    print(f"[OK] Extracted {len(raw_text)} characters of text")
    print(f"[INFO] First 200 chars: {raw_text[:200]}...")
    
    # Step 3: Send to LLM for summarization
    print(f"\nStep 3: Sending to LLM for summarization...")
    
    # Truncate to max chars
    from services.data_ingestion.edgar_worker import _get_edgar_config
    max_chars = _get_edgar_config("max_filing_chars_for_llm", 40000)
    text_to_summarize = raw_text[:max_chars]
    
    print(f"[INFO] Sending {len(text_to_summarize)} chars to LLM...")
    
    # Build prompt
    prompt = f"""Please summarize this SEC {test_filing['form']} filing for {SYMBOL}.

Focus on:
- Key financial results (revenue, profit, margins)
- Material events or changes
- Risk factors or concerns
- Forward-looking statements
- Any market-relevant information

Filing text (first {len(text_to_summarize)} chars):
{text_to_summarize}

Provide a concise 2-3 paragraph summary focusing on market-relevant information."""

    # Call LLM
    try:
        from services.sentiment.engine import SentimentEngine
        engine = SentimentEngine()
        
        response_data = await engine._call_ollama(
            prompt=prompt,
            model_override=engine.config.extraction_model if hasattr(engine, "config") else None,
            force_json=False,
        )
        
        if response_data:
            # Extract text from response
            if isinstance(response_data, dict):
                summary = response_data.get("response", response_data.get("text", str(response_data)))
            else:
                summary = str(response_data)
            
            print(f"\n[OK] LLM Summary generated!")
            print("=" * 60)
            print("LLM SUMMARY:")
            print("=" * 60)
            print(summary)
            print("=" * 60)
            
            # Save to database for testing
            print(f"\n[INFO] Saving summary to database...")
            from database.engine import SessionLocal
            from database.models import SecFiling
            
            db = SessionLocal()
            try:
                # Check if filing already exists
                existing = db.query(SecFiling).filter(
                    SecFiling.accession_number == accn
                ).first()
                
                if existing:
                    existing.raw_text = raw_text
                    existing.llm_summary = summary
                    existing.processed = True
                    existing.processed_at = datetime.now()
                    db.commit()
                    print(f"[OK] Updated existing filing in database")
                else:
                    print(f"[INFO] Filing not in database yet (run poll cycle first to store metadata)")
                    
            finally:
                db.close()
            
        else:
            print(f"[FAIL] LLM returned empty response")
            
    except Exception as exc:
        print(f"[FAIL] LLM call failed: {exc}")
        import traceback
        traceback.print_exc()


async def test_multiple_filings():
    """Test LLM reading of multiple 10-K and 10-Q filings."""
    print("\n" + "=" * 60)
    print(f"Testing LLM reading of multiple {SYMBOL} filings")
    print("=" * 60 + "\n")
    
    from services.data_ingestion.edgar_client import EdgarClient
    
    client = EdgarClient()
    cik = client.get_cik_for_ticker(SYMBOL)
    
    if not cik:
        print(f"[FAIL] Could not resolve CIK for {SYMBOL}")
        return
    
    # Fetch filings
    filings = client.get_recent_filings(
        cik=cik,
        form_types=FORM_TYPES,
        max_filings=6
    )
    
    if not filings:
        print(f"[FAIL] No filings found")
        return
    
    # Test up to 3 filings
    for i, filing in enumerate(filings[:3]):
        print(f"\n{'=' * 60}")
        print(f"Filing {i+1}: {filing['form']} {filing['filingDate']}")
        print(f"{'=' * 60}")
        
        raw_text = client.fetch_filing_text(
            cik,
            filing["accessionNumber"],
            filing["primaryDocument"]
        )
        
        if not raw_text:
            print(f"[SKIP] Could not extract text")
            continue
        
        print(f"[OK] Extracted {len(raw_text)} chars")
        
        # Call LLM
        from services.sentiment.engine import SentimentEngine
        engine = SentimentEngine()
        
        max_chars = 40000
        text_to_summarize = raw_text[:max_chars]
        
        prompt = f"""Summarize this SEC {filing['form']} filing for {SYMBOL} in 2-3 paragraphs.

Focus on market-relevant information: financial results, risk factors, material events, forward guidance.

Text ({len(text_to_summarize)} chars):
{text_to_summarize[:5000]}..."""  # Send first 5000 chars for quick test
        
        print(f"[INFO] Calling LLM...")
        response_data = await engine._call_ollama(
            prompt=prompt,
            model_override=engine.config.extraction_model,
            force_json=False,
        )
        
        if response_data:
            if isinstance(response_data, dict):
                summary = response_data.get("response", str(response_data))
            else:
                summary = str(response_data)
            
            print(f"\nLLM Summary (first 500 chars):")
            print(summary[:500])
            print("...\n")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("EDGAR LLM Reading Test")
    print("=" * 60 + "\n")
    
    try:
        # Test single filing (full text)
        asyncio.run(test_llm_read_filing())
        
        # Uncomment to test multiple filings (faster, truncated text)
        # asyncio.run(test_multiple_filings())
        
        print("\n" + "=" * 60)
        print("TEST COMPLETE")
        print("=" * 60)
        
    except Exception as exc:
        print(f"\n[FAILED] Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
