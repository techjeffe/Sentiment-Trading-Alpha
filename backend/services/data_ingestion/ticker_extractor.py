"""
Ticker Extraction Module
Ported from SignalScope's ticker-utils.ts

Automatically extracts stock tickers from unstructured text using regex patterns
and filters out common false positives using blacklists.
"""

import re
from typing import List, Set, Optional
from dataclasses import dataclass


# Regex pattern to match ticker symbols (1-5 uppercase letters)
TICKER_REGEX = r'\b([A-Z]{1,5})\b'

# Common English words and acronyms that look like tickers but aren't
BLACKLIST: Set[str] = {
    # Single/two-letter words (removed 'A' and 'I' - valid tickers)
    'AM', 'AN', 'AS', 'AT', 'BE', 'BY', 'DO', 'GO', 'IF', 'IN',
    'IS', 'IT', 'ME', 'MY', 'NO', 'OF', 'OK', 'ON', 'OR', 'SO', 'TO', 'UP',
    'US', 'WE', 'DD', 'TA', 'PE', 'SP',
    
    # Financial acronyms & market terms
    'CEO', 'IPO', 'ETF', 'SEC', 'FBI', 'FDA', 'IMO', 'YOLO', 'FOMO',
    'EPS', 'GDP', 'CPI', 'ATH', 'ATL', 'OTC', 'NYSE', 'NASDAQ',
    'USD', 'EUR', 'GBP', 'JPY',
    'NFA', 'DCA', 'ROI', 'DCF', 'VWAP', 'PCE', 'YTD', 'ITM', 'OTM', 'ATM',
    'SPX', 'DJI', 'VIX', 'APY', 'CFO', 'CTO', 'COO', 'CMO',
    'CFD', 'NAV', 'AUM', 'RFP', 'EOD', 'REIT', 'SPAC',
    
    # Common ETFs (not breakout candidates)
    'SPY', 'QQQ', 'IWM', 'DIA', 'MDY', 'IJR', 'VTI', 'VOO', 'VEA', 'VWO',
    'EEM', 'EFA', 'GLD', 'SLV', 'GDX', 'GDXJ', 'TLT', 'IEF', 'SHY', 'LQD',
    'HYG', 'JNK', 'XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU',
    'XLB', 'XLRE', 'XLC', 'ARKK', 'ARKG', 'ARKW', 'ARKF', 'ARKQ', 'ARKX',
    'SOXL', 'SOXS', 'TQQQ', 'SQQQ', 'SPXL', 'SPXS', 'UVXY', 'SVXY',
    'IAU', 'SLV', 'USO', 'UNG', 'PDBC', 'DBC', 'FXI', 'KWEB', 'MCHI',
    'RSP', 'QQQM', 'SCHD', 'VIG', 'DGRO', 'DVY', 'SDY', 'VYM', 'HDV',
    'IEMG', 'ACWI', 'URTH', 'VXUS', 'BND', 'AGG', 'BNDX', 'EMB', 'MUB',
    
    # Reddit/internet slang
    'WSB', 'HODL', 'TLDR', 'LMAO', 'ROFL', 'IMHO', 'AFAIK', 'NSFW', 'TIL',
    'PSA', 'IIRC', 'FYI', 'AMA',
    
    # Non-US markets/exchanges
    'TSX', 'TSXV', 'LSE', 'ASX', 'FTSE', 'DAX', 'NIKKEI',
    
    # Common 3-letter words
    'ALL', 'ARE', 'BUT', 'CAN', 'FOR', 'GET', 'HAS', 'HAD', 'HER', 'HIM',
    'HIS', 'HOW', 'ITS', 'LET', 'MAY', 'NEW', 'NOT', 'NOW', 'OLD', 'OUR',
    'OUT', 'OWN', 'SAY', 'SHE', 'THE', 'TOO', 'TWO', 'WAY', 'WHO', 'BOY',
    'DID', 'DON', 'GOT', 'HIT', 'HOT', 'LOT', 'MAN', 'PUT', 'RAN', 'RED',
    'RUN', 'SET', 'SIT', 'TOP', 'TRY', 'USE', 'WAS', 'WIN', 'WON', 'YET',
    'YOU', 'BIG', 'ANY', 'DAY', 'END', 'FAR', 'FEW', 'GAS',
    
    # Common 4-letter words
    'HIGH', 'LOW', 'LONG', 'JUST', 'VERY', 'MUCH', 'THAT', 'THIS', 'WHAT',
    'WHEN', 'WILL', 'WITH', 'HAVE', 'FROM', 'BEEN', 'SOME', 'THAN', 'THEM',
    'THEN', 'THEY', 'CALL', 'HOLD', 'SELL', 'PUMP', 'DUMP', 'MOON', 'BEAR',
    'BULL', 'GAIN', 'LOSS', 'EDIT', 'HOPE', 'BEST', 'POST', 'EVER', 'STOP',
    'GOOD', 'TAKE', 'MAKE', 'LIKE', 'NEXT', 'OVER', 'BACK', 'CASH', 'RISK',
    'FREE', 'HELP', 'HERE', 'LOOK', 'ONLY', 'REAL', 'SURE', 'WELL', 'DOWN',
    'SAME', 'OPEN', 'TELL', 'TRUE', 'TURN', 'KEEP', 'EVEN', 'LAST', 'MOVE',
    'PAYS', 'SAFE', 'SAVE', 'WORK',
    
    # Common 3-5 letter words (expanded)
    'TRIAL', 'PHASE', 'SHORT', 'GAMMA', 'DELTA', 'THETA', 'VEGA', 'ENTRY', 'EXIT',
    'WOW', 'GOAT', 'BEAT', 'HYPE', 'AUTO', 'ALSO', 'AWAY', 'COME', 'EACH',
    'ELSE', 'FEEL', 'FIND', 'FIVE', 'FOUR', 'FULL', 'GAVE', 'GONE', 'GROW',
    'HALF', 'HAND', 'HARD', 'HEAD', 'IDEA', 'INTO', 'KNEW', 'KNOW', 'LEFT',
    'LIFE', 'LINE', 'LIST', 'LIVE', 'MANY', 'MOST', 'MUST', 'NAME', 'NEED',
    'ONCE', 'PART', 'PAST', 'PLAN', 'PLAY', 'PULL', 'PURE', 'PUSH', 'READ',
    'REST', 'RISE', 'RULE', 'SEEN', 'SHOW', 'SIDE', 'SIGN', 'SIZE', 'TALK',
    'TEAM', 'TECH', 'TEND', 'THUS', 'TIME', 'TONE', 'TOLD', 'TOOK', 'TYPE',
    'UPON', 'USED', 'VAST', 'VIEW', 'VOTE', 'WAIT', 'WALK', 'WANT', 'WIDE',
    'WORD', 'YEAR', 'ZERO',
    
    # Crypto tickers (X/Twitter has heavy crypto discussion)
    'BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'ADA', 'AVAX', 'DOT', 'SHIB', 'PEPE',
    
    # EDGAR RSS / XML structural tokens
    'CIK', 'UTC', 'DIV', 'RSS', 'XML', 'HTTP', 'ATOM', 'HREF', 'HTML',
    
    # Common English words seen as false-positive tickers
    'AFTER', 'AGAIN', 'AGENT', 'ABOVE', 'BELOW', 'CALLS', 'COOL', 'DEAL',
    'DEBT', 'MORE', 'OUTTA', 'PAID', 'RIGHT', 'SAFER', 'SPLIT', 'START',
    'STILL', 'THANK', 'THESE', 'BEING', 'COULD', 'DOING', 'EVERY', 'GOING',
    'GREAT', 'LEAST', 'NEVER', 'OTHER', 'SHALL', 'SINCE', 'THEIR', 'THERE',
    'THING', 'THINK', 'THOSE', 'UNTIL', 'WATCH', 'WHERE', 'WHICH', 'WHILE',
    'WORLD', 'WOULD', 'ABOUT', 'POINT', 'MIGHT', 'MONEY', 'PRICE', 'SHARE',
    'STOCK', 'TODAY', 'TRADE', 'VALUE', 'WORTH', 'WHOLE', 'FIRST', 'GIVEN',
    
    # Financial/media acronyms
    'CNBC', 'FOMC', 'OPEC', 'OPEX', 'MACD', 'FINRA', 'GAAP', 'CAGR', 'MOASS',
    'COMEX', 'WSJ', 'GOP', 'NATO', 'DOJ', 'ECB', 'DXY', 'DARPA', 'IEEPA',
    'HYSA', 'IFRS',
    
    # Two-letter words
    'DR', 'ER', 'IM', 'IV', 'OP', 'PT', 'SA', 'SI', 'TL', 'DA', 'FX',
    'HR', 'PS', 'WW',
    
    # More false positives
    'SHIT', 'POSTS', 'HINT', 'IRAN', 'UAE', 'BMW', 'BASF',
    'LOTTO', 'PIPE', 'VLCC', 'ISM', 'GMT', 'NPV', 'FCF',
    'SWAP', 'REPO', 'CORP', 'BIAS', 'FLAT', 'HIKE', 'FUND', 'HEDGE', 'MACRO',
}

# Mega-cap stocks (too large for breakout detection, but valid tickers)
MEGA_CAPS: Set[str] = {
    'AAPL', 'MSFT', 'GOOG', 'GOOGL', 'AMZN', 'META', 'TSLA', 'NVDA',
    'BRK', 'JPM', 'V', 'MA', 'UNH', 'JNJ', 'WMT', 'PG',
}


@dataclass
class TickerMatch:
    """Represents a discovered ticker with context."""
    symbol: str
    confidence: float
    source_text: str
    position: int


def extract_tickers(text: str, filter_blacklist: bool = True) -> List[str]:
    """
    Extract stock tickers from unstructured text.
    
    Args:
        text: The text to search for tickers
        filter_blacklist: Whether to filter out blacklisted words
        
    Returns:
        List of unique ticker symbols found
    """
    if not text:
        return []
    
    # Find all matches for 1-5 uppercase letters
    matches = re.findall(TICKER_REGEX, text)
    
    # Remove duplicates while preserving order
    seen: Set[str] = set()
    unique_tickers: List[str] = []
    
    for ticker in matches:
        if ticker not in seen:
            seen.add(ticker)
            unique_tickers.append(ticker)
    
    # Apply filters
    if filter_blacklist:
        unique_tickers = [
            t for t in unique_tickers 
            if t not in BLACKLIST and len(t) >= 2  # Match SignalScope: length >= 2
        ]
    
    # Note: MEGA_CAPS are valid tickers, just filtered in some contexts
    # We don't filter them here, but scoring system can handle them
    
    return unique_tickers


def extract_tickers_from_article(title: str, content: str, 
                                 filter_blacklist: bool = True) -> List[str]:
    """
    Extract tickers from a news article (title + content).
    
    Args:
        title: Article title
        content: Article body text
        filter_blacklist: Whether to filter blacklisted words
        
    Returns:
        List of unique ticker symbols found
    """
    full_text = f"{title} {content}"
    return extract_tickers(full_text, filter_blacklist)


def extract_tickers_from_text_with_context(text: str, 
                                          filter_blacklist: bool = True) -> List[TickerMatch]:
    """
    Extract tickers with their context (surrounding text).
    
    Args:
        text: The text to search
        filter_blacklist: Whether to filter blacklisted words
        
    Returns:
        List of TickerMatch objects with symbol and context
    """
    if not text:
        return []
    
    matches = re.finditer(TICKER_REGEX, text)
    ticker_matches: List[TickerMatch] = []
    seen: Set[str] = set()
    
    for match in matches:
        ticker = match.group(1)
        
        if ticker in seen:
            continue
        
        if filter_blacklist and ticker in BLACKLIST:
            continue
        
        if filter_blacklist and len(ticker) < 2:
            continue
        
        seen.add(ticker)
        
        # Get surrounding context (50 chars before and after)
        start = max(0, match.start() - 50)
        end = min(len(text), match.end() + 50)
        context = text[start:end]
        
        ticker_matches.append(TickerMatch(
            symbol=ticker,
            confidence=0.8,  # Base confidence, can be adjusted by context
            source_text=context,
            position=match.start()
        ))
    
    return ticker_matches


def is_valid_ticker(ticker: str, strict: bool = False) -> bool:
    """
    Check if a string is a valid ticker symbol.
    
    Args:
        ticker: The string to check
        strict: If True, also filter mega-caps and common patterns
        
    Returns:
        True if valid ticker, False otherwise
    """
    if not ticker or not isinstance(ticker, str):
        return False
    
    # Must be 1-5 uppercase letters
    if not re.match(r'^[A-Z]{1,5}$', ticker):
        return False
    
    # Filter blacklist
    if ticker in BLACKLIST:
        return False
    
    # Strict mode: filter additional patterns
    if strict:
        # Filter mega-caps
        if ticker in MEGA_CAPS:
            return False
        
        # Filter single letters (too ambiguous)
        if len(ticker) == 1:
            return False
    
    return True


def filter_mega_caps(tickers: List[str]) -> List[str]:
    """
    Filter out mega-cap tickers from a list.
    
    Args:
        tickers: List of ticker symbols
        
    Returns:
        Filtered list without mega-caps
    """
    return [t for t in tickers if t not in MEGA_CAPS]


def extract_cashtag_tickers(text: str) -> List[str]:
    """
    Extract tickers from cashtag format (e.g., $AAPL, $TSLA).
    Common in Twitter/X posts.
    
    Args:
        text: Text containing cashtags
        
    Returns:
        List of ticker symbols (without $)
    """
    if not text:
        return []
    
    # Match $ followed by 1-5 uppercase letters
    cashtag_pattern = r'\$([A-Z]{1,5})\b'
    matches = re.findall(cashtag_pattern, text)
    
    # Filter and deduplicate
    seen: Set[str] = set()
    tickers: List[str] = []
    
    for ticker in matches:
        if ticker not in seen and ticker not in BLACKLIST:
            seen.add(ticker)
            tickers.append(ticker)
    
    return tickers


# Convenience function that combines multiple extraction methods
def extract_all_tickers(text: str, include_cashtags: bool = True) -> List[str]:
    """
    Extract tickers using all available methods.
    
    Args:
        text: Text to search
        include_cashtags: Whether to also extract cashtag format
        
    Returns:
        Combined list of unique tickers
    """
    # Standard extraction
    tickers = extract_tickers(text)
    
    # Add cashtags if requested
    if include_cashtags:
        cashtags = extract_cashtag_tickers(text)
        # Combine and deduplicate
        all_tickers = set(tickers + cashtags)
        tickers = list(all_tickers)
    
    return tickers
