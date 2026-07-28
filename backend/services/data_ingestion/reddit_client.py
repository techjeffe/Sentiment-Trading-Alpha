"""
Reddit Signal Scraper using PRAW
Ported from SignalScope's reddit.ts

Fetches stock mentions from Reddit across multiple subreddits,
extracts tickers, and returns structured signals for analysis.

Uses PRAW (Python Reddit API Wrapper) for reliable access.
Requires Reddit API credentials in environment variables:
  REDDIT_CLIENT_ID
  REDDIT_CLIENT_SECRET
  REDDIT_USER_AGENT (optional, defaults to sentiment-trading-alpha)
"""

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any
import asyncio

import praw
from praw.models import Submission

from services.data_ingestion.ticker_extractor import extract_tickers


# Subreddits to scan with their sort configurations
# Ported from SignalScope's SUBREDDIT_CONFIG
SUBREDDIT_CONFIG = [
    {"name": "wallstreetbets", "sorts": [
        {"type": "new", "limit": 25, "pages": 3},
        {"type": "rising", "limit": 15},
        {"type": "hot", "limit": 15}
    ]},
    {"name": "stocks", "sorts": [
        {"type": "new", "limit": 20, "pages": 2},
        {"type": "rising", "limit": 15},
        {"type": "hot", "limit": 15}
    ]},
    {"name": "investing", "sorts": [{"type": "new", "limit": 15}]},
    {"name": "pennystocks", "sorts": [
        {"type": "new", "limit": 15, "pages": 2},
        {"type": "rising", "limit": 10},
        {"type": "hot", "limit": 15}
    ]},
    {"name": "smallstreetbets", "sorts": [
        {"type": "new", "limit": 15},
        {"type": "rising", "limit": 10}
    ]},
    {"name": "options", "sorts": [
        {"type": "new", "limit": 15},
        {"type": "rising", "limit": 10}
    ]},
    {"name": "stockmarket", "sorts": [{"type": "new", "limit": 15}]},
    {"name": "ValueInvesting", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "spacs", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "weedstocks", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "Shortsqueeze", "sorts": [
        {"type": "new", "limit": 15},
        {"type": "rising", "limit": 10}
    ]},
    {"name": "RobinHoodPennyStocks", "sorts": [{"type": "new", "limit": 15}]},
    {"name": "Daytrading", "sorts": [
        {"type": "new", "limit": 15},
        {"type": "rising", "limit": 10}
    ]},
    {"name": "SwingTrading", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "biotech", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "SecurityAnalysis", "sorts": [{"type": "new", "limit": 10}]},
    {"name": "MillennialBets", "sorts": [{"type": "new", "limit": 10}]},
]

# Constants
MAX_COMMENT_FETCHES_PER_SUB = 3
COMMENT_ENGAGEMENT_THRESHOLD = 25


@dataclass
class RawSignal:
    """Represents a signal from a source (Reddit, SEC, etc.).
    Ported from SignalScope's RawSignal interface.
    """
    symbol: str
    source: str  # "REDDIT", "SEC_INSIDER", etc.
    title: Optional[str] = None
    body: Optional[str] = None
    url: Optional[str] = None
    author: Optional[str] = None
    upvotes: Optional[int] = None
    comment_count: Optional[int] = None
    subreddit: Optional[str] = None
    post_age: Optional[float] = None  # hours
    sort_type: Optional[str] = None  # "new", "rising", "hot", "comment"
    flair: Optional[str] = None
    author_age: Optional[int] = None
    author_karma: Optional[int] = None
    # Additional fields for other sources
    insider_title: Optional[str] = None
    purchase_value: Optional[float] = None
    option_type: Optional[str] = None
    net_premium: Optional[float] = None
    call_premium_ratio: Optional[float] = None
    volume_ratio: Optional[float] = None
    # Twitter-specific
    retweet_count: Optional[int] = None
    follower_count: Optional[int] = None
    # Polymarket-specific
    market_probability: Optional[float] = None
    # Metadata
    created_at: float = field(default_factory=lambda: time.time())
    raw_data: Dict[str, Any] = field(default_factory=dict)


def get_reddit_client() -> Optional[praw.Reddit]:
    """
    Create and return a PRAW Reddit client.
    
    Returns:
        PRAW Reddit instance or None if credentials not found
    """
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        print("Reddit API credentials not found.")
        print("Please set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables.")
        print("Get credentials at: https://www.reddit.com/prefs/apps")
        return None
    
    user_agent = os.getenv("REDDIT_USER_AGENT", "sentiment-trading-alpha:v1.0")
    
    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            read_only=True  # We only need to read posts
        )
        return reddit
    except Exception as e:
        print(f"Error creating Reddit client: {e}")
        return None


def process_reddit_post(submission: Submission, sort_type: str) -> List[RawSignal]:
    """
    Process a Reddit post and extract ticker signals.
    
    Args:
        submission: PRAW Submission object
        sort_type: Sort type (new, rising, hot)
        
    Returns:
        List of RawSignal objects
    """
    signals = []
    
    # Extract text from title and selftext
    title = submission.title or ""
    selftext = submission.selftext or ""
    text = f"{title} {selftext}"
    
    # Extract tickers
    tickers = extract_tickers(text)
    
    # Calculate post age
    post_age_hours = None
    if hasattr(submission, 'created_utc'):
        post_age_hours = (time.time() - submission.created_utc) / 3600
    
    # Create signals for each ticker
    for symbol in tickers:
        signal = RawSignal(
            symbol=symbol,
            source="REDDIT",
            title=title,
            body=selftext[:2000],
            url=f"https://reddit.com{submission.permalink}",
            author=str(submission.author) if submission.author else "unknown",
            upvotes=submission.score,
            comment_count=submission.num_comments,
            subreddit=str(submission.subreddit),
            post_age=post_age_hours,
            sort_type=sort_type,
            flair=submission.link_flair_text,
            created_at=time.time()
        )
        signals.append(signal)
    
    return signals


def fetch_subreddit_signals(reddit: praw.Reddit, 
                           subreddit_name: str, 
                           sort_config: Dict) -> List[RawSignal]:
    """
    Fetch signals from a single subreddit.
    
    Args:
        reddit: PRAW Reddit client
        subreddit_name: Name of subreddit
        sort_config: Sort configuration dict
        
    Returns:
        List of RawSignal objects
    """
    signals = []
    sort_type = sort_config["type"]
    limit = sort_config["limit"]
    pages = sort_config.get("pages", 1)
    
    try:
        subreddit = reddit.subreddit(subreddit_name)
        
        # Get posts based on sort type
        if sort_type == "new":
            posts = subreddit.new(limit=limit * pages)
        elif sort_type == "rising":
            posts = subreddit.rising(limit=limit)
        elif sort_type == "hot":
            posts = subreddit.hot(limit=limit)
        else:
            print(f"Unknown sort type: {sort_type}")
            return []
        
        # Process posts
        for submission in posts:
            post_signals = process_reddit_post(submission, sort_type)
            signals.extend(post_signals)
        
        print(f"Reddit r/{subreddit_name}/{sort_type}: fetched {len(signals)} signals")
        
    except Exception as e:
        print(f"Error fetching from r/{subreddit_name}/{sort_type}: {e}")
    
    return signals


def fetch_reddit_signals() -> List[RawSignal]:
    """
    Main function to fetch signals from all configured subreddits.
    
    Returns:
        List of RawSignal objects from Reddit
    """
    reddit = get_reddit_client()
    if not reddit:
        return []
    
    all_signals = []
    
    for config in SUBREDDIT_CONFIG:
        subreddit_name = config["name"]
        
        for sort_config in config["sorts"]:
            signals = fetch_subreddit_signals(reddit, subreddit_name, sort_config)
            all_signals.extend(signals)
            
            # Rate limiting: sleep between requests
            time.sleep(0.5)
    
    print(f"Reddit: total fetched {len(all_signals)} raw signals")
    return all_signals


if __name__ == "__main__":
    # Test the Reddit client
    print("Testing Reddit client with PRAW...")
    print("Note: Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables")
    print("Get credentials at: https://www.reddit.com/prefs/apps\n")
    
    signals = fetch_reddit_signals()
    print(f"\nTotal signals fetched: {len(signals)}")
    
    if signals:
        print("\nFirst 5 signals:")
        for i, signal in enumerate(signals[:5], 1):
            print(f"{i}. {signal.symbol} from r/{signal.subreddit}")
            print(f"   Title: {signal.title[:60]}...")
            print(f"   Upvotes: {signal.upvotes}, Comments: {signal.comment_count}")
