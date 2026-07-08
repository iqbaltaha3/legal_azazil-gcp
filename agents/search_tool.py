# agents/search_tool.py
"""
Web search tool via Tavily, for questions outside the CUAD/template corpus
(e.g. current statutes, recent case law, general legal questions).
"""
from tavily import TavilyClient
from config.settings import TAVILY_API_KEY

_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None


def web_search(query: str) -> str:
    if not _client:
        return "Web search is not configured (missing TAVILY_API_KEY)."
    if not query or not query.strip():
        return "No search query provided."

    try:
        results = _client.search(query=query, max_results=5)
    except Exception as e:
        return f"Web search failed: {e}"

    items = results.get("results", [])
    if not items:
        return "No web results found."

    formatted = []
    for r in items:
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = (r.get("content", "") or "")[:300]
        formatted.append(f"- {title} ({url})\n  {content}")

    return "\n\n".join(formatted)
