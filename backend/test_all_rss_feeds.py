"""
Test all RSS feeds and identify working vs non-working ones.
"""

import requests
import sys
import time

# RSS feeds to test (from news_sources.py)
FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/rss/topstories"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
    ("Reuters Markets", "https://www.reuters.com/markets/rss"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Investing.com", "https://www.investing.com/rss/news_301.rss"),
    ("Forex Factory", "https://www.forexfactory.com/feed/news"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB Press", "https://www.ecb.europa.eu/rss/ecb_press.rss"),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
]

def test_feed(name, url, timeout=10):
    """Test if an RSS feed is working."""
    try:
        response = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RSS Tester)'
        })
        
        if response.status_code == 200:
            # Check if it looks like RSS/XML
            content = response.text.lower()
            if '<?xml' in content or '<rss' in content or '<feed' in content:
                return True, "OK"
            else:
                return False, "Not RSS/XML content"
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, str(exc)

def main():
    print("=" * 60)
    print("RSS FEED VALIDATION TEST")
    print("=" * 60)
    print()
    
    working = []
    failed = []
    
    for name, url in FEEDS:
        print(f"Testing: {name}...", end=" ")
        success, message = test_feed(name, url)
        
        if success:
            print("[PASS]")
            working.append((name, url))
        else:
            print(f"[FAIL] - {message}")
            failed.append((name, url))
        
        time.sleep(0.5)  # Be nice to servers
    
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    print(f"\nWorking feeds ({len(working)}):")
    for name, url in working:
        print(f"  [PASS] {name}")
    
    print(f"\nFailed feeds ({len(failed)}):")
    for name, url in failed:
        print(f"  [FAIL] {name}")
        print(f"         {url}")
    
    print(f"\nSuccess rate: {len(working)}/{len(FEEDS)} ({len(working)*100/len(FEEDS):.1f}%)")
    
    # Write results to file for easy parsing
    with open('/tmp/rss_test_results.txt', 'w') as f:
        f.write("WORKING FEEDS:\n")
        for name, url in working:
            f.write(f"{name}|{url}\n")
        f.write("\nFAILED FEEDS:\n")
        for name, url in failed:
            f.write(f"{name}|{url}\n")
    
    print("\nResults saved to /tmp/rss_test_results.txt")

if __name__ == "__main__":
    main()
