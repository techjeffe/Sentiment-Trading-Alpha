"""
Test all RSS feeds in the configuration and identify working vs non-working ones.
"""

import requests
import sys
import time
from typing import List, Tuple

# Add backend to path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.news_sources import get_all_sources

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
                # Count items
                item_count = content.count('<item>') + content.count('<entry>')
                return True, f"OK ({item_count} items)"
            else:
                return False, "Not RSS/XML content"
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, str(exc)

def main():
    print("=" * 60)
    print("COMPREHENSIVE RSS FEED VALIDATION TEST")
    print("=" * 60)
    print()
    
    sources = get_all_sources()
    
    working = []
    failed = []
    
    print(f"Testing {len(sources)} feeds...\n")
    
    for source in sources:
        print(f"Testing: {source.name} ({source.category})...", end=" ")
        success, message = test_feed(source.name, source.url)
        
        if success:
            print(f"[PASS] - {message}")
            working.append(source)
        else:
            print(f"[FAIL] - {message}")
            failed.append(source)
        
        time.sleep(0.3)  # Be nice to servers
    
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    print(f"\nWorking feeds ({len(working)}):")
    for source in working:
        print(f"  [PASS] {source.name} ({source.category})")
    
    print(f"\nFailed feeds ({len(failed)}):")
    for source in failed:
        print(f"  [FAIL] {source.name} ({source.category})")
        print(f"         {source.url}")
    
    print(f"\nSuccess rate: {len(working)}/{len(sources)} ({len(working)*100/len(sources):.1f}%)")
    
    # Write results to file
    with open('/tmp/rss_validation_results.txt', 'w') as f:
        f.write("WORKING FEEDS:\n")
        for source in working:
            f.write(f"{source.name}|{source.url}|{source.category}\n")
        f.write("\nFAILED FEEDS:\n")
        for source in failed:
            f.write(f"{source.name}|{source.url}|{source.category}\n")
    
    print("\nResults saved to /tmp/rss_validation_results.txt")
    
    return working, failed

if __name__ == "__main__":
    working, failed = main()
