"""
Unit Tests for Ticker Extraction Module
Tests ported from SignalScope's ticker-utils.ts functionality
"""

import sys
import os
from pathlib import Path

# Add the backend directory to the Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services.data_ingestion.ticker_extractor import (
    extract_tickers,
    extract_tickers_from_article,
    extract_tickers_from_text_with_context,
    is_valid_ticker,
    filter_mega_caps,
    extract_cashtag_tickers,
    extract_all_tickers,
    TickerMatch,
    BLACKLIST,
    MEGA_CAPS
)


class TestExtractTickers:
    """Test the basic extract_tickers function."""
    
    def test_simple_ticker_extraction(self):
        """Test extracting simple ticker symbols."""
        text = "AAPL is going up, MSFT is down"
        result = extract_tickers(text)
        assert "AAPL" in result
        assert "MSFT" in result
        assert len(result) == 2
    
    def test_ticker_with_numbers(self):
        """Test that tickers with numbers are not extracted (1-5 letters only)."""
        text = "BT1C is not a valid ticker"
        result = extract_tickers(text)
        assert "BT1C" not in result
    
    def test_blacklist_filtering(self):
        """Test that blacklisted words are filtered out."""
        text = "The CEO said the IPO is coming, but ATH is far"
        result = extract_tickers(text)
        assert "CEO" not in result
        assert "IPO" not in result
        assert "ATH" not in result
    
    def test_etf_filtering(self):
        """Test that common ETFs are filtered."""
        text = "SPY and QQQ are ETFs, but AAPL is a stock"
        result = extract_tickers(text)
        assert "SPY" not in result
        assert "QQQ" not in result
        assert "AAPL" in result
    
    def test_duplicate_removal(self):
        """Test that duplicate tickers are removed."""
        text = "AAPL AAPL MSFT AAPL"
        result = extract_tickers(text)
        assert result.count("AAPL") == 1
        assert len(result) == 2
    
    def test_empty_text(self):
        """Test handling of empty text."""
        result = extract_tickers("")
        assert result == []
    
    def test_none_text(self):
        """Test handling of None text."""
        result = extract_tickers(None)  # type: ignore
        assert result == []
    
    def test_lowercase_conversion(self):
        """Test that lowercase is converted to uppercase."""
        # Note: Our regex only matches uppercase, but let's document this
        text = "aapl is not matched by uppercase regex"
        result = extract_tickers(text)
        assert "AAPL" not in result  # lowercase won't match
    
    def test_ticker_length_limits(self):
        """Test that only 2-5 letter tickers are extracted (matching SignalScope)."""
        text = "A AB ABC ABCD ABCDE ABCDEF"
        result = extract_tickers(text)
        assert "A" not in result  # Single letter filtered (length >= 2)
        assert "AB" in result
        assert "ABC" in result
        assert "ABCD" in result
        assert "ABCDE" in result
        assert "ABCDEF" not in result  # 6 letters, should not match


class TestExtractTickersFromArticle:
    """Test the article extraction function."""
    
    def test_title_and_content(self):
        """Test extraction from title and content combined."""
        title = "AAPL reports earnings"
        content = "Apple Inc. (AAPL) beat expectations. MSFT also up."
        result = extract_tickers_from_article(title, content)
        assert "AAPL" in result
        assert "MSFT" in result
    
    def test_deduplication_across_title_content(self):
        """Test that tickers appearing in both title and content are deduplicated."""
        title = "AAPL earnings"
        content = "AAPL beat expectations"
        result = extract_tickers_from_article(title, content)
        assert result.count("AAPL") == 1


class TestExtractTickersWithContext:
    """Test extraction with context."""
    
    def test_context_extraction(self):
        """Test that context is captured around ticker."""
        text = "The company " + "A" * 50 + " AAPL is doing well " + "B" * 50
        result = extract_tickers_from_text_with_context(text)
        
        assert len(result) > 0
        match = result[0]
        assert isinstance(match, TickerMatch)
        assert match.symbol == "AAPL"
        assert len(match.source_text) > 0
        assert match.position >= 0
    
    def test_confidence_score(self):
        """Test that confidence score is set."""
        text = "AAPL is up"
        result = extract_tickers_from_text_with_context(text)
        assert len(result) > 0
        assert result[0].confidence == 0.8


class TestIsValidTicker:
    """Test ticker validation."""
    
    def test_valid_ticker(self):
        """Test valid ticker symbols."""
        assert is_valid_ticker("AAPL") == True
        assert is_valid_ticker("MSFT") == True
        assert is_valid_ticker("Z") == True  # Single letter valid
    
    def test_invalid_ticker_too_long(self):
        """Test ticker that's too long."""
        assert is_valid_ticker("ABCDEF") == False
    
    def test_invalid_ticker_lowercase(self):
        """Test lowercase ticker."""
        assert is_valid_ticker("aapl") == False
    
    def test_invalid_ticker_with_numbers(self):
        """Test ticker with numbers."""
        assert is_valid_ticker("AAPL1") == False
    
    def test_blacklisted_ticker(self):
        """Test blacklisted words."""
        assert is_valid_ticker("CEO") == False
        assert is_valid_ticker("IPO") == False
    
    def test_strict_mode_mega_caps(self):
        """Test strict mode filters mega-caps."""
        assert is_valid_ticker("AAPL", strict=True) == False
        assert is_valid_ticker("MSFT", strict=True) == False
    
    def test_strict_mode_single_letter(self):
        """Test strict mode filters single letters."""
        # Single letters are already filtered by extract_tickers (length >= 2)
        # In strict mode, they're also invalid
        assert is_valid_ticker("X", strict=True) == False
        assert is_valid_ticker("I", strict=True) == False
        assert is_valid_ticker("A", strict=True) == False


class TestFilterMegaCaps:
    """Test mega-cap filtering."""
    
    def test_filter_mega_caps(self):
        """Test that mega-caps are filtered out."""
        tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "XYZ"]
        result = filter_mega_caps(tickers)
        assert "AAPL" not in result
        assert "MSFT" not in result
        assert "XYZ" in result  # Not a mega-cap


class TestExtractCashtagTickers:
    """Test cashtag extraction (Twitter/X format)."""
    
    def test_basic_cashtags(self):
        """Test basic $TICKER format."""
        text = "$AAPL is up, $MSFT is down"
        result = extract_cashtag_tickers(text)
        assert "AAPL" in result
        assert "MSFT" in result
    
    def test_cashtag_with_blacklist(self):
        """Test that cashtag blacklist works."""
        text = "$CEO announced $IPO"
        result = extract_cashtag_tickers(text)
        assert "CEO" not in result
        assert "IPO" not in result
    
    def test_mixed_cashtag_and_regular(self):
        """Test text with both cashtag and regular tickers."""
        text = "$AAPL and MSFT"
        cashtags = extract_cashtag_tickers(text)
        regular = extract_tickers(text)
        assert "AAPL" in cashtags
        assert "MSFT" in regular
        # Note: AAPL also appears in regular extraction (uppercase matches both)
        # This is correct behavior - $AAPL and AAPL both match the ticker pattern
        assert "AAPL" in regular  # Both formats match


class TestExtractAllTickers:
    """Test the combined extraction function."""
    
    def test_combined_extraction(self):
        """Test that both formats are extracted."""
        text = "$AAPL reports earnings, MSFT also up"
        result = extract_all_tickers(text)
        assert "AAPL" in result
        assert "MSFT" in result
    
    def test_deduplication(self):
        """Test that duplicates are removed across methods."""
        text = "$AAPL AAPL"
        result = extract_all_tickers(text)
        assert result.count("AAPL") == 1


class TestEdgeCases:
    """Test edge cases and real-world scenarios."""
    
    def test_reddit_post_format(self):
        """Test typical Reddit post format."""
        text = "Just bought 100 shares of GME @ $200. DD inside."
        result = extract_tickers(text)
        # GME should be found, DD is blacklisted
        assert "GME" in result
        assert "DD" not in result
    
    def test_sec_filing_format(self):
        """Test SEC filing text format."""
        text = "Form 4 filing: John Smith purchased 10,000 shares of AAPL on 2024-01-15"
        result = extract_tickers(text)
        assert "AAPL" in result
    
    def test_news_headline_format(self):
        """Test news headline with multiple tickers."""
        text = "AAPL, MSFT, and GOOGL rally on strong earnings"
        result = extract_tickers(text)
        assert "AAPL" in result
        assert "MSFT" in result
        assert "GOOGL" in result
    
    def test_ticker_with_period(self):
        """Test ticker followed by period."""
        text = "AAPL. is a great stock"
        result = extract_tickers(text)
        # The regex \b should handle word boundaries correctly
        assert "AAPL" in result
    
    def test_ticker_in_parentheses(self):
        """Test ticker in parentheses."""
        text = "Apple Inc. (AAPL) reported earnings"
        result = extract_tickers(text)
        assert "AAPL" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
