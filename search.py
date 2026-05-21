"""
Web research agent — fetches DuckDuckGo snippets for a market question.

Runs two targeted queries:
  1. Direct question — what's actually happening
  2. Recent news angle — latest developments

Deduplicates results and returns a formatted block for the AI prompt.
No API key needed.
"""

from ddgs import DDGS

MAX_RESULTS_PER_QUERY = 4
MAX_CHARS_PER_SNIPPET = 280
MAX_TOTAL_CHARS = 1800


def _run_query(query: str) -> list:
    try:
        return DDGS().text(query, max_results=MAX_RESULTS_PER_QUERY) or []
    except Exception as e:
        print(f"  [search] Query failed: {e}")
        return []


def fetch_context(question: str) -> str:
    """
    Run 2 targeted searches and return formatted research block.
    Returns empty string on any failure — bot always continues.
    """
    queries = [
        question,
        f"{question} latest update 2025",
    ]

    all_results = []
    seen = set()

    for query in queries:
        for r in _run_query(query):
            title = r.get("title", "")
            if title in seen:
                continue
            seen.add(title)
            all_results.append(r)

    if not all_results:
        return ""

    lines = []
    total = 0
    for r in all_results:
        snippet = (r.get("body") or "").strip()[:MAX_CHARS_PER_SNIPPET]
        if not snippet:
            continue
        title = r.get("title", "Source")
        date = r.get("published", "")
        date_str = f" | {date}" if date else ""
        line = f"• [{title}{date_str}] {snippet}"
        total += len(line)
        if total > MAX_TOTAL_CHARS:
            break
        lines.append(line)

    if not lines:
        return ""

    return "=== Research ===\n" + "\n".join(lines) + "\n================"
