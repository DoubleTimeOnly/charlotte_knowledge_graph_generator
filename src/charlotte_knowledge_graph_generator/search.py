"""Tavily web search integration.

SearchService runs 2-3 queries in parallel and returns deduplicated results.
All errors are caught and logged — callers always get a (possibly empty) list[SearchResult].

Pipeline:
  queries: list[str]
      │
      ▼ asyncio.gather(*[_query(q) for q in queries], return_exceptions=True)
  per-query results (or Exception)
      │
      ▼ flatten + deduplicate by URL + filter non-http(s) URLs
  list[SearchResult]  (may be empty on total failure)
"""

import asyncio
import logging

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchService:
    def __init__(self, api_key: str, max_results: int = 5) -> None:
        self._api_key = api_key
        self._max_results = max_results

    async def search(self, queries: list[str]) -> list[SearchResult]:
        """Run queries in parallel; return deduplicated results. Returns [] on total failure."""
        gathered = await asyncio.gather(
            *[self._query(q) for q in queries], return_exceptions=True
        )
        seen_urls: set[str] = set()
        results: list[SearchResult] = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.warning("Search query failed: %s", item)
                continue
            for r in item:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    results.append(r)
        logger.info("Search: %d deduplicated results from %d queries", len(results), len(queries))
        return results

    async def _query(self, query: str) -> list[SearchResult]:
        """Single Tavily search. Raises on error (caught by asyncio.gather)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": self._max_results,
                    "include_raw_content": False,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchResult(
                    title=r["title"],
                    url=r["url"],
                    snippet=r.get("content", ""),
                )
                for r in data.get("results", [])
                if r.get("url", "").startswith(("http://", "https://"))
            ]

    @staticmethod
    def format_context(results: list[SearchResult]) -> str:
        """Format as numbered list: [1] Title — Snippet (URL)"""
        if not results:
            return "(no search results available)"
        lines = [f"[{i + 1}] {r.title} — {r.snippet} ({r.url})" for i, r in enumerate(results)]
        return "\n".join(lines)
