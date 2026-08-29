"""
RSS Feed Compatibility Tester
==============================
Tests whether a news article URL can be successfully fetched and extracted
using the same approach as the application (requests + trafilatura.extract).

Use this to verify a new RSS feed source before adding it to the app config.

Usage:
    python test_rss_feed_compatibility.py <url> [url2 url3 ...]

Examples:
    python test_rss_feed_compatibility.py https://www.bbc.com/news/articles/cjep78l5835o
    python test_rss_feed_compatibility.py https://www.cnbc.com/2026/05/05/after-coinbase-prediction-markets-traders-see-more-tech-layoffs.html
    python test_rss_feed_compatibility.py https://techcrunch.com/2026/05/11/digg-tries-again-this-time-as-an-ai-news-aggregator/
"""
import sys
import trafilatura
import requests

MIN_EXTRACTED_CHARS = 400


def test_url(url: str) -> dict:
    """Test a single URL and return results."""
    result = {
        "url": url,
        "status": "unknown",
        "html_chars": 0,
        "extracted_chars": 0,
        "extracted_preview": "",
        "error": None,
    }

    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        resp.raise_for_status()
        html = resp.text
        result["html_chars"] = len(html)

        extracted = trafilatura.extract(
            html,
            favor_recall=True,
            include_comments=False,
            include_tables=False,
            url=url,
        ) or ""

        result["extracted_chars"] = len(extracted)
        result["extracted_preview"] = extracted[:300] if extracted else ""

        if extracted and len(extracted.strip()) >= MIN_EXTRACTED_CHARS:
            result["status"] = "PASS"
        elif extracted:
            result["status"] = "LOW_CONTENT"
        else:
            result["status"] = "EMPTY"

    except requests.exceptions.HTTPError as e:
        result["status"] = "BLOCKED"
        result["error"] = f"HTTP {e.response.status_code}: Site blocked the request (bot protection / paywall)"
    except requests.exceptions.ConnectionError:
        result["status"] = "CONNECTION_ERROR"
        result["error"] = "Could not connect to the URL"
    except requests.exceptions.Timeout:
        result["status"] = "TIMEOUT"
        result["error"] = "Request timed out after 15 seconds"
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = f"{type(e).__name__}: {e}"

    return result


def print_result(result: dict, index: int = 0):
    """Print a formatted test result."""
    status_colors = {
        "PASS": "✅ PASS",
        "LOW_CONTENT": "⚠️  LOW CONTENT",
        "EMPTY": "❌ EMPTY",
        "BLOCKED": "🔒 BLOCKED",
        "CONNECTION_ERROR": "💥 CONNECTION ERROR",
        "TIMEOUT": "⏱️  TIMEOUT",
        "ERROR": "❌ ERROR",
        "unknown": "❓ UNKNOWN",
    }

    label = f"Test #{index}" if index else "Result"
    status_str = status_colors.get(result["status"], f"❓ {result['status']}")

    print(f"\n{'='*60}")
    print(f"  {label}: {status_str}")
    print(f"{'='*60}")
    print(f"  URL: {result['url']}")
    print(f"  HTML downloaded: {result['html_chars']:,} chars")

    if result["extracted_chars"] > 0:
        print(f"  Article text extracted: {result['extracted_chars']:,} chars")
        print(f"  Meets minimum ({MIN_EXTRACTED_CHARS} chars): {'✅ YES' if result['extracted_chars'] >= MIN_EXTRACTED_CHARS else '❌ NO'}")
        print(f"\n  Preview: {result['extracted_preview']}...")
    else:
        print(f"  Article text extracted: 0 chars")

    if result["error"]:
        print(f"\n  Error: {result['error']}")

    if result["status"] == "PASS":
        print(f"\n  ✅ RECOMMENDATION: This site works well. Safe to add to RSS feeds.")
    elif result["status"] == "BLOCKED":
        print(f"\n  ❌ RECOMMENDATION: This site blocks automated requests.")
        print(f"     Do NOT add this feed. Find an alternative source.")
    elif result["status"] in ("EMPTY", "LOW_CONTENT"):
        print(f"\n  ⚠️  RECOMMENDATION: Trafilatura could not extract meaningful content.")
        print(f"     The RSS description text will be used as fallback.")
    else:
        print(f"\n  ⚠️  RECOMMENDATION: Test failed. Investigate before adding this feed.")


def main():
    urls = sys.argv[1:] if len(sys.argv) > 1 else []

    if not urls:
        # Demo mode: run against known-good and known-bad examples
        print("=" * 60)
        print("  RSS Feed Compatibility Tester")
        print("=" * 60)
        print()
        print("  Usage: python test_rss_feed_compatibility.py <url1> [url2 url3 ...]")
        print()
        print("  Running demo with example URLs...")
        print()

        urls = [
            "https://www.bbc.com/news/articles/cjep78l5835o",
            "https://techcrunch.com/2026/05/11/digg-tries-again-this-time-as-an-ai-news-aggregator/",
            "https://www.npr.org/2026/05/11/nx-s1-5716202/discount-groceries-aldi-food-affordability",
            "https://www.cnbc.com/2026/05/05/after-coinbase-prediction-markets-traders-see-more-tech-layoffs.html",
            "https://www.fastcompany.com/91539853/the-strange-reason-dua-lipa-is-suing-samsung-for-15-million",
        ]

    passed = 0
    failed = 0
    blocked = 0

    for i, url in enumerate(urls, 1):
        result = test_url(url)
        print_result(result, i)

        if result["status"] == "PASS":
            passed += 1
        elif result["status"] == "BLOCKED":
            blocked += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"  SUMMARY: {passed} passed, {failed} failed, {blocked} blocked")
    print(f"{'='*60}")
    print()

    if blocked > 0:
        print("  🔒 Blocked sites use bot protection (DataDome, Cloudflare, etc.)")
        print("     that cannot be bypassed with simple HTTP requests.")
        print("     Recommendation: Remove these feeds and find alternatives.")
    if failed > 0:
        print("  ❌ Failed sites may have structural issues or be temporarily unavailable.")
        print("     Try again later or find alternative sources.")

    return 0 if passed > 0 and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())