"""
Compatibility Utilities
Helps RawSignal dataclass work with code expecting dicts.
"""

from services.data_ingestion.reddit_client import RawSignal


def signal_to_dict(signal: RawSignal) -> dict:
    """
    Convert RawSignal dataclass to dict.
    
    Args:
        signal: RawSignal object
        
    Returns:
        Dict representation
    """
    return {
        "symbol": signal.symbol,
        "source": signal.source,
        "title": signal.title,
        "body": signal.body,
        "url": signal.url,
        "author": signal.author,
        "upvotes": signal.upvotes,
        "comment_count": signal.comment_count,
        "subreddit": signal.subreddit,
        "post_age": signal.post_age,
        "sort_type": signal.sort_type,
        "flair": signal.flair,
        "insider_title": signal.insider_title,
        "purchase_value": signal.purchase_value,
        "option_type": signal.option_type,
        "net_premium": signal.net_premium,
        "volume_ratio": signal.volume_ratio,
        "retweet_count": signal.retweet_count,
        "follower_count": signal.follower_count,
        "market_probability": signal.market_probability,
        "created_at": signal.created_at,
        "raw_data": signal.raw_data
    }


def get_signal_attr(signal, attr: str, default=None):
    """
    Get attribute from signal (works with both dict and RawSignal).
    
    Args:
        signal: Dict or RawSignal
        attr: Attribute name
        default: Default value if not found
        
    Returns:
        Attribute value
    """
    if isinstance(signal, dict):
        return signal.get(attr, default)
    else:
        return getattr(signal, attr, default)
