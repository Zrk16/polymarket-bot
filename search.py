"""
Web search helper — fetches DuckDuckGo snippets for a market question.
Free, no API key needed. Used to give the AI real-world context before analysis.
"""

from duckduckgo_search import DDGS

MAX_RESULTS = 4
MAX_CHARS_PER_SNIPPET = 300
MAX_TOTAL_CHARS = 900


def fetch_context(question: str) -> str:
    """
    Search DuckDuckGo for the market question and return a formatted
    block of snippets to inject into the AI prompt.
    Returns empty string on any failure so the bot always continues.
    """
    try:
        query = f"{question} 2025"
        results = DDGS().text(query, max_results=MAX_RESULTS)

        if not results:
            return ""

        lines = []
        total = 0
        for r in results:
            snippet = (r.get("body") or "").strip()
            if not snippet:
                continue
            snippet = snippet[:MAX_CHARS_PER_SNIPPET]
            source = r.get("title", "")
            line = f"- [{source}] {snippet}"
            total += len(line)
            if total > MAX_TOTAL_CHARS:
                break
            lines.append(line)

        if not lines:
            return ""

        return "Recent web search results:\n" + "\n".join(lines)

    except Exception as e:
        print(f"  [search] Failed: {e}")
        return ""
