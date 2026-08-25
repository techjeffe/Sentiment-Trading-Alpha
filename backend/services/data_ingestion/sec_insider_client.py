"""
SEC Insider Trading Scraper
Ported from SignalScope's sec-insider.ts

Fetches insider trading data from OpenInsider.com,
filters for significant C-suite/director purchases,
and returns structured signals for analysis.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import requests
from bs4 import BeautifulSoup

# Import RawSignal from reddit_client (single source of truth)
from services.data_ingestion.reddit_client import RawSignal

from services.data_ingestion.ticker_extractor import extract_tickers, BLACKLIST

from config.logic_loader import LOGIC


# Fallback minimum purchase value ($100K default) when logic_config.json
# does not define sec_insider.min_purchase_value.
MIN_PURCHASE_VALUE = 100000


def _sec_insider_config() -> dict:
    """Read the sec_insider materiality settings from logic_config.json."""
    cfg = LOGIC.get("sec_insider") or {}
    return {
        "enabled": cfg.get("enabled", True),
        "min_purchase_value": cfg.get("min_purchase_value", MIN_PURCHASE_VALUE),
        "require_c_suite_or_director": cfg.get("require_c_suite_or_director", True),
        "lookback_days": int(cfg.get("lookback_days", 7)),
        "max_signals": int(cfg.get("max_signals", 100)),
    }
C_SUITE_TITLES = {
    "CEO", "CFO", "COO", "CTO", "CMO", "CIO", "CISO", "CLO",
    "President", "Chairman", "Vice Chairman",
    "Dir", "Director",
    "Gen Counsel", "General Counsel",
    "EVP", "SVP", "VP",
    "Chief Executive Officer", "Chief Financial Officer",
    "Chief Operating Officer", "Chief Technology Officer",
}


@dataclass
class InsiderRow:
    """Represents a row from OpenInsider table."""
    filing_date: str
    trade_date: str
    ticker: str
    company_name: str
    insider_name: str
    insider_title: str
    trade_type: str
    price: float
    qty: int
    value: float


def is_c_suite_or_director(title: str) -> bool:
    """
    Check if a title is C-suite or director level.
    
    Args:
        title: Insider title string
        
    Returns:
        True if C-suite or director
    """
    normalized = title.strip()
    for t in C_SUITE_TITLES:
        if t in normalized:
            return True
    return False


def parse_open_insider_html(html: str) -> List[InsiderRow]:
    """
    Parse OpenInsider HTML table to extract insider trades.
    Uses BeautifulSoup for reliable parsing.
    
    Args:
        html: Raw HTML from OpenInsider
        
    Returns:
        List of InsiderRow objects
    """
    rows = []
    soup = BeautifulSoup(html, 'html.parser')
    
    # Find the tinytable
    table = soup.find('table', class_='tinytable')
    if not table:
        return rows
    
    # Find all rows (skip header row)
    data_rows = table.find_all('tr')[1:]  # Skip header
    
    for row in data_rows:
        cells = row.find_all('td')
        if len(cells) < 13:
            continue
        
        # Extract trade type from cell 7
        trade_type = cells[7].get_text(strip=True)
        
        # Only open market purchases ("P - Purchase")
        if not trade_type.startswith("P"):
            continue
        
        # Extract ticker from cell 3
        ticker_cell = cells[3]
        ticker_link = ticker_cell.find('a')
        if ticker_link:
            ticker = ticker_link.get_text(strip=True)
        else:
            ticker = ticker_cell.get_text(strip=True)
        
        # Validate ticker
        if not ticker or len(ticker) > 5 or ticker in BLACKLIST:
            continue
        
        # Extract values from cells
        try:
            filing_date = cells[1].get_text(strip=True)
            trade_date = cells[2].get_text(strip=True)
            company_name = cells[4].get_text(strip=True)
            insider_name = cells[5].get_text(strip=True)
            insider_title = cells[6].get_text(strip=True)
            
            price_text = cells[8].get_text(strip=True).replace('$', '').replace(',', '')
            qty_text = cells[9].get_text(strip=True).replace(',', '')
            value_text = cells[12].get_text(strip=True).replace('$', '').replace(',', '')
            
            price = float(price_text)
            qty = int(qty_text) if qty_text else 0
            value = float(value_text)
            
        except (ValueError, IndexError) as e:
            continue
        
        # Create InsiderRow
        insider_row = InsiderRow(
            filing_date=filing_date,
            trade_date=trade_date,
            ticker=ticker,
            company_name=company_name,
            insider_name=insider_name,
            insider_title=insider_title,
            trade_type=trade_type,
            price=price,
            qty=qty,
            value=value
        )
        rows.append(insider_row)
    
    return rows


def fetch_from_open_insider(lookback_days: Optional[int] = None) -> List[RawSignal]:
    """
    Fetch insider trading data from OpenInsider.com.

    Args:
        lookback_days: Override for the filing lookback window (defaults to
            logic_config.json sec_insider.lookback_days).

    Returns:
        List of RawSignal objects from SEC insider trades
    """
    cfg = _sec_insider_config()
    if not cfg["enabled"]:
        return []

    days = lookback_days or cfg["lookback_days"]

    # OpenInsider URL: open market purchases filed in last N days
    url = (f"http://openinsider.com/screener?s=&o=&pl=50&ph=&ll=&lh=&fd={days}&fdr=&td=0&tdr=&feession=0"
           "&cession=0&sid=1&iession=0&ession=0&otype=&othertype=&ression=0&sortcol=0"
           f"&cnt={cfg['max_signals']}&page=1")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        # OpenInsider uses HTTP only (HTTPS refused)
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        html = response.text
        all_rows = parse_open_insider_html(html)
        
        # Materiality filter: configurable minimum purchase value and title level.
        filtered = [
            row for row in all_rows
            if row.value >= cfg["min_purchase_value"]
            and (not cfg["require_c_suite_or_director"] or is_c_suite_or_director(row.insider_title))
        ]
        
        # Convert to RawSignal objects
        signals = []
        for row in filtered:
            signal = RawSignal(
                symbol=row.ticker,
                source="SEC_INSIDER",
                title=f"Insider Purchase: {row.insider_name}",
                body=f"{row.insider_title} purchased {row.qty} shares at ${row.price:.2f} "
                     f"(${row.value:,.0f} total) on {row.trade_date}",
                url=f"http://openinsider.com/screener?s={row.ticker}",
                author=row.insider_name,
                insider_title=row.insider_title,
                purchase_value=row.value,
                created_at=time.time(),
                raw_data={
                    "filing_date": row.filing_date,
                    "trade_date": row.trade_date,
                    "company_name": row.company_name,
                    "price": row.price,
                    "qty": row.qty,
                    "value": row.value
                }
            )
            signals.append(signal)
        
        print(f"SEC Insider: fetched {len(signals)} insider purchase signals")
        return signals
        
    except requests.RequestException as e:
        print(f"OpenInsider fetch error: {e}")
        return []
    except Exception as e:
        print(f"OpenInsider parse error: {e}")
        return []


def fetch_sec_insider_signals(lookback_days: Optional[int] = None) -> List[RawSignal]:
    """
    Main function to fetch SEC insider trading signals.

    Args:
        lookback_days: Override for the OpenInsider filing lookback window.

    Returns:
        List of RawSignal objects from SEC insider trades
    """
    return fetch_from_open_insider(lookback_days=lookback_days)


def store_sec_insider_signals(db=None, lookback_days: Optional[int] = None) -> List[RawSignal]:
    """
    Fetch SEC insider signals and persist them to the insider_signals table.

    Rows are deduplicated via a unique_key (symbol + insider + trade date + qty
    + price) using INSERT OR IGNORE, so re-runs accumulate history without
    creating duplicates. This lets the news page and the trade-list source
    modal surface the SEC source/link after the fact, even for symbols that
    fall out of OpenInsider's rolling window.

    Args:
        db: Optional SQLAlchemy session. A new session is created if None.
        lookback_days: Override for the OpenInsider filing lookback window.

    Returns:
        The RawSignal objects that passed the materiality filter (for scoring).
    """
    signals = fetch_sec_insider_signals(lookback_days=lookback_days)
    if not signals:
        return signals

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from database.models import InsiderSignal

    own_session = db is None
    if db is None:
        from database.engine import SessionLocal
        db = SessionLocal()

    try:
        rows = []
        for s in signals:
            raw = s.raw_data or {}
            trade_date = str(raw.get("trade_date") or "")
            key = f"{s.symbol}|{s.author or ''}|{trade_date}|{raw.get('qty') or ''}|{raw.get('price') or ''}"
            rows.append({
                "symbol": s.symbol,
                "company_name": raw.get("company_name"),
                "insider_name": s.author or "",
                "insider_title": s.insider_title or "",
                "trade_type": "P",
                "price": raw.get("price"),
                "qty": raw.get("qty"),
                "value": raw.get("value"),
                "filing_date": raw.get("filing_date"),
                "trade_date": trade_date or None,
                "url": s.url,
                "source_link": s.url,
                "unique_key": key,
            })

        if rows:
            stmt = sqlite_insert(InsiderSignal).prefix_with("OR IGNORE")
            db.execute(stmt, rows)
            db.commit()
            print(f"SEC Insider: stored {len(rows)} insider signals")

        return signals
    finally:
        if own_session:
            db.close()


if __name__ == "__main__":
    # Test the SEC insider client
    print("Testing SEC Insider client...")
    print("Fetching insider trading data from OpenInsider.com\n")
    
    signals = fetch_sec_insider_signals()
    print(f"\nTotal insider signals fetched: {len(signals)}")
    
    if signals:
        print("\nFirst 5 signals:")
        for i, signal in enumerate(signals[:5], 1):
            print(f"{i}. {signal.symbol} - {signal.insider_title}")
            print(f"   {signal.body[:80]}...")
            print(f"   Value: ${signal.purchase_value:,.0f}")
