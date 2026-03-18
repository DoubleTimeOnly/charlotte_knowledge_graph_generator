"""GraphService — business logic for knowledge graph generation.

generate_graph() pipeline:
  topic_str
      │
      ▼ validate (handled by Pydantic at API layer)
  force_refresh?
      │ YES → skip cache, go straight to QUERY_GEN
      │ NO
      ▼ CacheLayer.get_graph()
  HIT ──────────────────────────────────► return GraphResponse
  MISS
      │
      ▼ LLMClient.generate_search_queries(topic)  ← fast, no tool use
  FALLBACK on error → [topic]
      │
      ▼ SearchService.search(queries)  ← parallel Tavily queries
  FALLBACK on error → []
      │
      ▼ LLMClient.generate_graph(topic, depth, search_results)
  LLMRefusalError?  ──────────────────► re-raise (API returns 422)
  GraphGenerationError? ───────────────► re-raise (API returns 503)
  APITimeoutError? ────────────────────► retry up to MAX_RETRIES, then re-raise
      │
      ▼ attach generated_at + sources to GraphResponse
      ▼ CacheLayer.set_graph()  (OperationalError → log + skip)
      ▼ return GraphResponse

_merge_graphs():
  base.nodes:     [A, B, C]
  addition.nodes: [C, D, E]  ← C already exists (by id)
  result.nodes:   [A, B, C, D, E]  ← C deduplicated
"""

import asyncio
import logging
from datetime import UTC, datetime

import anthropic

from charlotte_knowledge_graph_generator.cache import CacheLayer
from charlotte_knowledge_graph_generator.config import Settings
from charlotte_knowledge_graph_generator.llm import (
    GraphGenerationError,
    LLMClientProtocol,
    LLMRefusalError,
)
from charlotte_knowledge_graph_generator.models import (
    GraphResponse,
    NodeDetail,
    NodeType,
    SearchResult,
    SubGraphResponse,
)
from charlotte_knowledge_graph_generator.search import SearchService

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BASE_DELAY = 2.0  # seconds


async def _with_retry(coro, *args):
    """Retry a coroutine on APITimeoutError or RateLimitError with exponential backoff."""
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await coro(*args)
        except anthropic.APITimeoutError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning("LLM timeout (attempt %d/%d), retrying in %.1fs", attempt + 1, MAX_RETRIES, delay)
                await asyncio.sleep(delay)
        except anthropic.RateLimitError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = float(getattr(exc, "retry_after", None) or RETRY_BASE_DELAY * (2**attempt))
                logger.warning("LLM rate limited (attempt %d/%d), retrying in %.1fs", attempt + 1, MAX_RETRIES, delay)
                await asyncio.sleep(delay)
    raise last_exc


def _merge_graphs(base: GraphResponse, addition: SubGraphResponse) -> GraphResponse:
    """Merge a sub-graph into the base graph, deduplicating nodes by id."""
    existing_ids = {n.id for n in base.nodes}
    new_nodes = [n for n in addition.nodes if n.id not in existing_ids]

    # Only include edges whose both endpoints exist in the merged graph
    all_ids = existing_ids | {n.id for n in new_nodes}
    new_edges = [
        e for e in addition.edges if e.source in all_ids and e.target in all_ids
    ]

    return GraphResponse(
        nodes=base.nodes + new_nodes,
        edges=base.edges + new_edges,
        topic=base.topic,
    )


class GraphService:
    def __init__(
        self,
        llm: LLMClientProtocol,
        cache: CacheLayer,
        settings: Settings,
        search: SearchService | None = None,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._settings = settings
        self._search = search

    async def generate_graph(
        self, topic: str, depth: int, force_refresh: bool = False
    ) -> GraphResponse:
        if not force_refresh:
            cached = await self._cache.get_graph(topic, depth, self._settings.prompt_version)
            if cached is not None:
                logger.info("Cache hit for topic=%r depth=%d", topic, depth)
                return cached

        logger.info("Cache miss for topic=%r depth=%d — calling LLM", topic, depth)

        # Web search (optional — skipped when SearchService not configured)
        search_results: list[SearchResult] = []
        if self._search is not None:
            try:
                queries = await self._llm.generate_search_queries(topic)
                logger.info(f"Generated the following queries for topic={topic!r}: {queries}")
            except Exception:
                logger.warning("Query generation failed for topic=%r, using topic as query", topic)
                queries = [topic]
            try:
                search_results = await self._search.search(queries)
                logger.info("Search returned %d results for topic=%r", len(search_results), topic)
            except Exception:
                logger.warning("Search failed for topic=%r, continuing with LLM-only", topic)
                search_results = []

        graph = await _with_retry(self._llm.generate_graph, topic, depth, search_results)

        # Attach metadata
        graph = graph.model_copy(
            update={
                "sources": search_results,
                "generated_at": datetime.now(UTC),
            }
        )

        # Enforce server-side node cap
        if len(graph.nodes) > self._settings.max_nodes_per_graph:
            logger.info(
                "Trimming graph from %d to %d nodes", len(graph.nodes), self._settings.max_nodes_per_graph
            )
            allowed_ids = {n.id for n in graph.nodes[: self._settings.max_nodes_per_graph]}
            graph = graph.model_copy(
                update={
                    "nodes": graph.nodes[: self._settings.max_nodes_per_graph],
                    "edges": [e for e in graph.edges if e.source in allowed_ids and e.target in allowed_ids],
                }
            )

        await self._cache.set_graph(topic, depth, self._settings.prompt_version, graph)
        return graph

    async def expand_node(
        self,
        node_label: str,
        node_type: NodeType,
        context_nodes: list[str],
        current_graph: GraphResponse,
    ) -> GraphResponse:
        addition = await _with_retry(
            self._llm.expand_node, node_label, node_type, context_nodes
        )

        # Enforce expansion cap
        if len(addition.nodes) > self._settings.max_nodes_per_expand:
            capped_ids = {n.id for n in addition.nodes[: self._settings.max_nodes_per_expand]}
            addition = SubGraphResponse(
                nodes=addition.nodes[: self._settings.max_nodes_per_expand],
                edges=[e for e in addition.edges if e.source in capped_ids and e.target in capped_ids],
            )

        merged = _merge_graphs(current_graph, addition)
        logger.info(
            "Expanded node=%r: added %d nodes, %d edges (total: %d nodes)",
            node_label,
            len(addition.nodes),
            len(addition.edges),
            len(merged.nodes),
        )
        return merged

    async def get_node_detail(
        self,
        label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> NodeDetail:
        cached = await self._cache.get_node_detail(
            label, node_type.value, self._settings.prompt_version
        )
        if cached is not None:
            logger.info("Cache hit for node detail label=%r", label)
            return cached

        detail = await _with_retry(
            self._llm.get_node_detail, label, node_type, context_nodes
        )
        await self._cache.set_node_detail(
            label, node_type.value, self._settings.prompt_version, detail
        )
        return detail
