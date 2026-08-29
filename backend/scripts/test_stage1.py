"""
Quick Stage 1 extraction smoke test.
Run from the backend directory:
    python test_stage1.py
Or with a specific extraction model:
    python test_stage1.py llama3.2:latest

Tests both built-in symbols (static keyword map) and custom symbols (LLM keyword generation).
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import List


@dataclass
class FakePost:
    title: str
    content: str = ""
    keywords: List[str] = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


# Headlines that should be caught by built-in symbol keywords
BUILTIN_HEADLINES = [
    "Iran threatens to block Strait of Hormuz as oil tensions rise",     # USO (oil)
    "Bitcoin ETF sees record inflows as institutional demand surges",     # BITO (bitcoin)
    "Federal Reserve signals potential rate cuts in next meeting",        # SPY/QQQ (Fed)
    "New tariffs on Chinese semiconductors announced by Commerce Dept",   # QQQ (chips)
    "OPEC+ agrees to extend production cuts through Q3",                  # USO (OPEC)
    "SEC approves new crypto trading regulations",                        # BITO (crypto)
    "S&P 500 hits all-time high on strong jobs report",                   # SPY (S&P 500)
]

# Headlines that should be caught by LLM-generated keywords for custom symbols
CUSTOM_HEADLINES = [
    "Nvidia announces next-gen Blackwell GPU architecture at GTC",        # NVDA
    "ServiceNow expands enterprise AI platform with new workflow tools",  # NOW
    "Jensen Huang unveils AI superchip targeting datacenter deployments", # NVDA (CEO name)
]

NOISE_HEADLINES = [
    "Local sports team wins championship game",
    "Celebrity couple spotted at film premiere",
]

ALL_HEADLINES = BUILTIN_HEADLINES + CUSTOM_HEADLINES + NOISE_HEADLINES
BUILTIN_SYMBOLS = ["USO", "BITO", "QQQ", "SPY"]
CUSTOM_SYMBOLS = ["NVDA", "NOW"]
ALL_SYMBOLS = BUILTIN_SYMBOLS + CUSTOM_SYMBOLS


async def run():
    from services.sentiment.engine import SentimentEngine

    model = sys.argv[1] if len(sys.argv) > 1 else "llama3.2:latest"
    print(f"\n=== Stage 1 smoke test — extraction model: {model} ===\n")
    print(f"Built-in symbols : {', '.join(BUILTIN_SYMBOLS)} (static keyword map — no LLM call)")
    print(f"Custom symbols   : {', '.join(CUSTOM_SYMBOLS)} (LLM generates keywords once, then cached)\n")

    posts = [FakePost(title=h) for h in ALL_HEADLINES]
    engine = SentimentEngine()

    result = await engine.extract_relevant_articles(
        posts=posts,
        symbols=ALL_SYMBOLS,
        extraction_model=model,
    )

    filtered = result["filtered_posts"]
    proxy_terms = result["proxy_terms_by_symbol"]

    print(f"\nResult: {len(filtered)}/{len(posts)} articles marked relevant\n")

    print("Relevant headlines:")
    for post in filtered:
        print(f"  ✓ {post.title}")

    not_relevant = [p for p in posts if p not in filtered]
    if not_relevant:
        print("\nFiltered out:")
        for post in not_relevant:
            print(f"  ✗ {post.title}")

    print("\nProxy terms by symbol:")
    for sym, terms in proxy_terms.items():
        source = "(static)" if sym in BUILTIN_SYMBOLS else "(LLM-generated)"
        sample = ", ".join(terms[:6]) + ("..." if len(terms) > 6 else "")
        print(f"  {sym} {source}: {sample or '(none)'}")

    # Pass/fail checks
    sports_filtered = all(
        "sports" not in p.title.lower() and "celebrity" not in p.title.lower()
        for p in filtered
    )
    builtin_posts = [p for p in posts if p.title in BUILTIN_HEADLINES]
    custom_posts  = [p for p in posts if p.title in CUSTOM_HEADLINES]

    builtin_caught = sum(1 for p in builtin_posts if p in filtered)
    custom_caught  = sum(1 for p in custom_posts  if p in filtered)

    custom_has_keywords = all(
        len(proxy_terms.get(sym, [])) > 1 for sym in CUSTOM_SYMBOLS
    )

    not_zero        = len(filtered) > 0
    enough_builtin  = builtin_caught >= 5   # expect at least 5/7 builtin headlines caught
    any_custom      = custom_caught > 0     # at least some custom headlines caught

    print(f"\n{'✓ PASS' if not_zero       else '✗ FAIL'}: At least 1 article found relevant (got {len(filtered)})")
    print(f"{'✓ PASS' if enough_builtin  else '⚠ WARN'}: Built-in symbols caught ≥5 financial headlines ({builtin_caught}/{len(builtin_posts)})")
    print(f"{'✓ PASS' if any_custom      else '✗ FAIL'}: Custom symbols (NVDA/NOW) caught at least 1 article ({custom_caught}/{len(custom_posts)})")
    print(f"{'✓ PASS' if custom_has_keywords else '✗ FAIL'}: LLM generated keywords for custom symbols (>1 term each)")
    print(f"{'✓ PASS' if sports_filtered else '✗ FAIL'}: Noise headlines (sports/celebrity) correctly filtered out")

    if not not_zero:
        print("\n⚠  Stage 1 returned 0 relevant articles — Stage 2 will fall back to all articles.")
        print("   This is safe but means no filtering. Check Ollama is running and the model is loaded.")

    if not any_custom:
        print(f"\n⚠  Custom symbol headlines were not caught. Generated keywords:")
        for sym in CUSTOM_SYMBOLS:
            print(f"   {sym}: {proxy_terms.get(sym, [])}")
        print("   Try a larger model (7B+) for better custom symbol coverage.")


if __name__ == "__main__":
    asyncio.run(run())
