"""
SEC EDGAR filings client for polling company filings and extracting document text.

This module provides a client for the SEC EDGAR JSON APIs:
- CIK resolution via company_tickers.json
- Submissions API for recent filings
- Filing document retrieval and text extraction

Follows the same patterns as MarketValidationClient (requests.Session,
trust_env=False, timeout, caching).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

# Trafilatura for HTML→text extraction (already a repo dependency)
try:
    import trafilatura
except ImportError:
    trafilatura = None


class EdgarClient:
    """
    Client for SEC EDGAR APIs with CIK caching and rate limiting.

    Required: compliant User-Agent header (SEC requirement).
    Rate limit: max ~10 requests/second.
    """

    # SEC EDGAR API endpoints
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/index.json"
    FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{filename}"

    # Cache for company tickers (CIK resolution)
    _tickers_cache: Optional[Dict[str, Any]] = None
    _tickers_cache_loaded_at: Optional[datetime] = None
    _tickers_cache_ttl_seconds = 86400  # 24 hours

    def __init__(self, user_agent: Optional[str] = None, timeout: int = 10):
        """
        Initialize the EDGAR client.

        Args:
            user_agent: Compliant SEC User-Agent string.
                        Falls back to EDGAR_USER_AGENT env var, then a default.
            timeout: Request timeout in seconds.
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

        # SEC requires a descriptive User-Agent
        self.user_agent = user_agent or os.getenv(
            "EDGAR_USER_AGENT",
            "Sentiment Trading Alpha admin@example.com"
        )
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })

        # Simple in-memory cache for filings (avoid re-fetching within a poll cycle)
        self._filing_cache: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
        self._filing_cache_ttl_seconds = 3600  # 1 hour

    def get_cik_for_ticker(self, ticker: str) -> Optional[str]:
        """
        Resolve a stock ticker to its SEC CIK (Central Index Key).

        Returns the CIK as a 10-digit zero-padded string, or None if not found.
        """
        ticker = ticker.upper().strip()

        # Load tickers cache if needed
        if not self._tickers_cache or self._cache_expired(
            self._tickers_cache_loaded_at,
            self._tickers_cache_ttl_seconds
        ):
            self._load_company_tickers()

        if not self._tickers_cache:
            return None

        # Search in cache (tickers cache is keyed by index, not ticker)
        for entry in self._tickers_cache.values():
            if entry.get("ticker", "").upper() == ticker:
                cik = str(entry.get("cik_str", ""))
                return cik.zfill(10)  # Zero-pad to 10 digits

        return None

    def _load_company_tickers(self) -> None:
        """Fetch and cache the company_tickers.json file."""
        try:
            response = self.session.get(self.COMPANY_TICKERS_URL, timeout=self.timeout)
            response.raise_for_status()
            self._tickers_cache = response.json()
            self._tickers_cache_loaded_at = datetime.now(timezone.utc)
        except Exception as exc:
            print(f"[EdgarClient] Failed to load company tickers: {exc}")
            self._tickers_cache = None

    def get_recent_filings(
        self,
        cik: str,
        form_types: Optional[List[str]] = None,
        max_filings: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent filings for a company via the Submissions API.

        Args:
            cik: 10-digit zero-padded CIK string.
            form_types: List of form types to filter (e.g., ["10-K", "10-Q", "8-K"]).
                        If None, returns all recent filings.
            max_filings: Maximum number of filings to return.

        Returns:
            List of filing dicts with keys: accessionNumber, filingDate, form,
            items, primaryDocument, primaryDocDescription, etc.
        """
        cik = cik.zfill(10)
        cache_key = f"filings_{cik}_{','.join(sorted(form_types or []))}"

        # Check cache
        cached = self._filing_cache.get(cache_key)
        if cached and (datetime.now(timezone.utc) - cached[0]).total_seconds() < self._filing_cache_ttl_seconds:
            return cached[1]

        try:
            url = self.SUBMISSIONS_URL.format(cik=cik)
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            filings = []
            recent = data.get("filings", {}).get("recent", {})

            if not recent:
                return []

            # Arrays are parallel — index i across all arrays describes one filing
            accession_numbers = recent.get("accessionNumber", [])
            forms = recent.get("form", [])
            filing_dates = recent.get("filingDate", [])
            report_dates = recent.get("reportDate", [])
            items_list = recent.get("items", [])
            primary_docs = recent.get("primaryDocument", [])
            descriptions = recent.get("primaryDocDescription", [])

            for i in range(min(len(accession_numbers), max_filings)):
                form_type = forms[i] if i < len(forms) else ""

                # Filter by form type if specified
                if form_types and form_type not in form_types:
                    continue

                filing = {
                    "accessionNumber": accession_numbers[i],
                    "filingDate": filing_dates[i] if i < len(filing_dates) else None,
                    "reportDate": report_dates[i] if i < len(report_dates) else None,
                    "form": form_type,
                    "items": items_list[i] if i < len(items_list) else "",
                    "primaryDocument": primary_docs[i] if i < len(primary_docs) else "",
                    "description": descriptions[i] if i < len(descriptions) else "",
                    "cik": cik,
                }
                filings.append(filing)

            # Cache the result
            self._filing_cache[cache_key] = (datetime.now(timezone.utc), filings)
            return filings

        except Exception as exc:
            print(f"[EdgarClient] Failed to fetch filings for CIK {cik}: {exc}")
            return []

    def fetch_filing_text(
        self,
        cik: str,
        accession_number: str,
        primary_document: str
    ) -> Optional[str]:
        """
        Fetch and extract text from a filing document.

        Args:
            cik: 10-digit zero-padded CIK.
            accession_number: Filing accession number (with dashes).
            primary_document: Primary document filename from submissions API.

        Returns:
            Extracted text content, or None if extraction failed.
        """
        if not trafilatura:
            print("[EdgarClient] trafilatura not available — cannot extract filing text")
            return None

        try:
            # Strip dashes from accession number for the URL
            accn_stripped = accession_number.replace("-", "")

            # Build the filing document URL
            url = self.FILING_DOC_URL.format(
                cik=cik.lstrip("0"),  # CIK without leading zeros in path
                accn=accn_stripped,
                filename=primary_document
            )

            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            # Extract text using trafilatura (reuse existing repo dependency)
            text = trafilatura.extract(
                response.content,
                include_comments=False,
                include_tables=False,  # SEC filings have heavy tables — skip for now
                output_format="txt"
            )

            return text

        except Exception as exc:
            print(f"[EdgarClient] Failed to fetch filing text for {accession_number}: {exc}")
            return None

    def fetch_filing_text_by_sections(
        self,
        cik: str,
        accession_number: str,
        primary_document: str,
        sections: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Fetch filing and extract specific sections (more targeted than full text).

        Args:
            cik: 10-digit zero-padded CIK.
            accession_number: Filing accession number.
            primary_document: Primary document filename.
            sections: List of section identifiers to extract.
                      For 10-K: ["Item 1", "Item 1A", "Item 7"]
                      For 10-Q: ["Item 2", "Item 4"]
                      For 8-K: None (use full text for short filings)

        Returns:
            Extracted text for the requested sections, or full text if sections is None.
        """
        # For now, just fetch full text — section extraction from SEC HTML
        # requires more sophisticated parsing (would need to parse <div>, <span>
        # with specific IDs or regex patterns for section headers)
        return self.fetch_filing_text(cik, accession_number, primary_document)

    @staticmethod
    def _cache_expired(loaded_at: Optional[datetime], ttl_seconds: int) -> bool:
        """Check if a cache entry has expired."""
        if not loaded_at:
            return True
        return (datetime.now(timezone.utc) - loaded_at).total_seconds() > ttl_seconds

    @staticmethod
    def is_material_8k(items: str, material_items: Optional[List[str]] = None) -> bool:
        """
        Check if an 8-K filing's item codes indicate material information.

        Args:
            items: Comma-separated item codes from the submissions API.
            material_items: List of material item codes. Defaults to common material items.

        Returns:
            True if the filing contains material items.
        """
        if not items:
            return False

        if material_items is None:
            material_items = ["2.02", "5.02", "7.01", "8.01"]  # Common material 8-K items

        filing_items = [item.strip() for item in items.split(",")]
        return any(item in material_items for item in filing_items)


def test_edgar_client():
    """
    Basic test function for EdgarClient.
    Run with: python -m services.data_ingestion.edgar_client
    """
    print("Testing EdgarClient...")

    client = EdgarClient()

    # Test CIK resolution
    print("\n1. Testing CIK resolution for NVDA...")
    cik = client.get_cik_for_ticker("NVDA")
    if cik:
        print(f"   [OK] NVDA CIK: {cik}")
    else:
        print("   [FAIL] Failed to resolve NVDA CIK")

    # Test fetching recent filings
    if cik:
        print("\n2. Testing recent filings fetch...")
        filings = client.get_recent_filings(cik, form_types=["10-K", "10-Q", "8-K"], max_filings=5)
        if filings:
            print(f"   [OK] Fetched {len(filings)} filings")
            for f in filings[:3]:
                print(f"      - {f['form']} {f['filingDate']} (accn: {f['accessionNumber']})")
        else:
            print("   [FAIL] No filings returned")

    print("\nEdgarClient test complete.")


if __name__ == "__main__":
    test_edgar_client()
