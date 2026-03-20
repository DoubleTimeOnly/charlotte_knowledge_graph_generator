"""Source backends for knowledge graph generation.

Two Tavily-backed backends:
  SearchService         — basic/advanced search (queries → snippets)
  TavilyResearchBackend — autonomous research  (topic  → synthesized overview + sources)

Pipeline diagrams:

SearchService.search():
  queries: list[str]
      │
      ▼ asyncio.gather(*[_query(q) for q in queries], return_exceptions=True)
  per-query results (or Exception)
      │
      ▼ flatten + deduplicate by URL + filter non-http(s) URLs
  list[SearchResult]  (may be empty on total failure)

TavilyResearchBackend.research():
  topic: str
      │
      ▼ POST /research  → {request_id, status: "pending"}  (HTTP 201)
      │
      ▼ poll GET /research/{request_id}  every RESEARCH_POLL_INTERVAL_SECS
  {status: "pending"|"in_progress"}  → keep polling
  {status: "failed"}                 → raise RuntimeError
  {status: "completed"}              → {content: str, sources: [{title, url, favicon}]}
      │
      ├── truncate content to MAX_RESEARCH_CHARS
      ├── filter non-http(s) source URLs
      ▼
  tuple[str | None, list[SearchResult]]
      │ on any http/timeout/parse error:
      └── raises — GraphService catches and falls back to LLM-only
"""

import asyncio
import logging

import httpx

from charlotte_knowledge_graph_generator.models import SearchResult

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_RESEARCH_URL = "https://api.tavily.com/research"

MAX_RESEARCH_CHARS = 100000  # Truncation limit for research overviews injected into prompts
MAX_SNIPPET_CHARS = 400  # Tavily content field can be full article text; keep prompts lean
RESEARCH_POLL_INTERVAL_SECS = 3  # How often to poll for research completion


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


class TavilyResearchBackend:
    """Autonomous multi-step research backend using the Tavily Research API.

    research() initiates an async research task, then polls until it completes.

    POST /research  → {request_id}
    GET  /research/{request_id}  → poll until status "completed" or "failed"

    Completed response shape:
        {"status": "completed", "content": "<synthesized prose>", "sources": [{"title": ..., "url": ..., "favicon": ...}]}

    Raises on any error — GraphService catches and falls back to LLM-only.
    """

    def __init__(self, api_key: str, timeout_secs: int = 120) -> None:
        self._api_key = api_key
        self._timeout_secs = timeout_secs

    async def research(self, topic: str) -> tuple[str | None, list[SearchResult]]:
        """Research a topic. Returns (overview, sources) or raises on failure."""
        async with httpx.AsyncClient(timeout=float(self._timeout_secs)) as client:
            # Step 1: initiate the research task
            resp = await client.post(
                TAVILY_RESEARCH_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"input": topic, "model": "mini"},
            )
            resp.raise_for_status()
            request_id = resp.json()["request_id"]
            logger.info("Research initiated: request_id=%s topic=%r", request_id, topic)

            # Step 2: poll until completed or failed
            poll_url = f"{TAVILY_RESEARCH_URL}/{request_id}"
            while True:
                await asyncio.sleep(RESEARCH_POLL_INTERVAL_SECS)
                poll_resp = await client.get(
                    poll_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                poll_resp.raise_for_status()
                data = poll_resp.json()
                status = data.get("status", None)
                logger.debug("Research poll: request_id=%s status=%s", request_id, status)
                if status == "completed":
                    break
                elif status == "failed":
                    raise RuntimeError(f"Tavily research failed for request_id={request_id}")
                elif status is None:
                    raise RuntimeError(f"Unexpected response shape during research polling: {data}")
                # pending / in_progress — keep polling

        overview = data.get("content") or ""
        logger.debug("Raw research overview for topic=%r: %r", topic, overview)
        if len(overview) > MAX_RESEARCH_CHARS:
            logger.warning(
                "Research overview truncated: %d → %d chars for topic=%r",
                len(overview),
                MAX_RESEARCH_CHARS,
                topic,
            )
            overview = overview[:MAX_RESEARCH_CHARS]

        sources = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:MAX_SNIPPET_CHARS],
            )
            for r in data.get("sources", [])
            if r.get("url", "").startswith(("http://", "https://"))
        ]

        overview_or_none = overview if overview else None
        logger.info(
            "Research: completed for topic=%r, overview_len=%d, sources=%d",
            topic,
            len(overview),
            len(sources),
        )
        return overview_or_none, sources
