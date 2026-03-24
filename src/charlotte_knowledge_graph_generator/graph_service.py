"""GraphService — business logic for knowledge graph generation.

generate_graph() pipeline:
  topic_str, mode
      │
      ▼ validate (handled by Pydantic at API layer)
  mode == "readwise"?
      │ YES ──────────────────────────────────────────────────────────────────
      │   ReadwiseSourceBackend.resolve_book_id(topic)  ← cheap, 1 API call
      │   CacheLayer.get_graph(f"readwise:{book_id}", ...)
      │   HIT ────────────────────────────────────► return GraphResponse
      │   MISS
      │     ReadwiseSourceBackend.fetch(topic)         ← full fetch + context
      │       ReadwiseAuthError  → re-raise as LLMRefusalError → 422
      │       ReadwiseBookNotFoundError → 422
      │       ReadwiseNoHighlightsError → 422
      │     LLMClient.generate_graph_from_highlights(book_title, highlights)
      │     attach resolved_title + generated_at
      │     CacheLayer.set_graph(f"readwise:{book_id}", ...)
      │     return GraphResponse
      │
      ▼ mode == "web_search" (default path)
  force_refresh?
      │ YES → skip cache
      │ NO
      ▼ CacheLayer.get_graph()
  HIT ──────────────────────────────────► return GraphResponse
  MISS
      │
      ▼ IF research_backend set:
      │     TavilyResearchBackend.research(topic)  ← autonomous multi-step (10-60s)
      │     on error → raise GraphGenerationError
      │ ELIF search_backend set:
      │     LLMClient.generate_search_queries(topic)  ← fast, no tool use
      │     FALLBACK on error → [topic]
      │     SearchService.search(queries)  ← parallel Tavily queries
      │     on error → raise GraphGenerationError
      │
      ▼ LLMClient.generate_graph(topic, depth, search_results, research_overview)
  LLMRefusalError?  ──────────────────► re-raise (API returns 422)
  GraphGenerationError? ───────────────► re-raise (API returns 503)
  APITimeoutError? ────────────────────► retry up to MAX_RETRIES, then re-raise
      │
      ▼ attach generated_at + sources to GraphResponse
      ▼ CacheLayer.set_graph()  (OperationalError → log + skip)
      ▼ return GraphResponse

expand_node() pipeline:
  node_label, node_type, context_nodes, current_graph
      │
      ▼ CacheLayer.get_expansion(node_label, node_type, prompt_version)
  HIT ──────────────────────────────────► cap → merge → return GraphResponse
  MISS
      │
      ▼ IF search_backend set:
      │     SearchService.search([node_label])  ← 1 query
      │     on error → raise GraphGenerationError
      │
      ▼ LLMClient.expand_node(label, type, ctx_nodes, search_context)
  LLMRefusalError?  ──────────────────► re-raise (API returns 422)
  GraphGenerationError? ───────────────► re-raise (API returns 503)
  APITimeoutError? ────────────────────► retry up to MAX_RETRIES, then re-raise
      │
      ▼ cap to max_nodes_per_expand
      ▼ CacheLayer.set_expansion()  (OperationalError → log + skip)
      ▼ _merge_graphs(current_graph, addition)
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
from charlotte_knowledge_graph_generator.sources import (
    ReadwiseAuthError,
    ReadwiseBookNotFoundError,
    ReadwiseNoHighlightsError,
    ReadwiseSourceBackend,
    SearchService,
    TavilyResearchBackend,
)

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
    """Merge a sub-graph into the base graph, deduplicating nodes and edges by id."""
    existing_ids = {n.id for n in base.nodes}
    new_nodes = [n for n in addition.nodes if n.id not in existing_ids]

    # Only include edges whose both endpoints exist in the merged graph
    all_ids = existing_ids | {n.id for n in new_nodes}
    existing_edge_keys = {(e.source, e.target) for e in base.edges}
    new_edges = [
        e for e in addition.edges
        if e.source in all_ids
        and e.target in all_ids
        and (e.source, e.target) not in existing_edge_keys
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
        research_backend: TavilyResearchBackend | None = None,
        readwise: ReadwiseSourceBackend | None = None,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._settings = settings
        self._search = search
        self._research_backend = research_backend
        self._readwise = readwise

    async def generate_graph(
        self, topic: str, depth: int, force_refresh: bool = False, mode: str = "web_search"
    ) -> GraphResponse:
        if mode == "readwise":
            return await self._generate_graph_readwise(topic, depth, force_refresh)
        return await self._generate_graph_web(topic, depth, force_refresh)

    async def _generate_graph_readwise(
        self, topic: str, depth: int, force_refresh: bool
    ) -> GraphResponse:
        """Readwise path: fetch highlights → LLM → cache keyed on resolved book_id."""
        if self._readwise is None:
            raise LLMRefusalError("Readwise is not configured (no READWISE_API_KEY)")

        # Step 1: resolve book_id cheaply (1 API call) so we can check the cache
        try:
            book_id = await self._readwise.resolve_book_id(topic)
        except ReadwiseAuthError as exc:
            raise LLMRefusalError("Invalid Readwise API key") from exc
        except ReadwiseBookNotFoundError as exc:
            raise LLMRefusalError(f"Book not found in Readwise: {topic!r}") from exc

        cache_key = f"readwise:{book_id}"

        if not force_refresh:
            cached = await self._cache.get_graph(cache_key, depth, self._settings.prompt_version)
            if cached is not None:
                logger.info("Cache hit for Readwise book_id=%d", book_id)
                return cached

        logger.info("Cache miss for Readwise book_id=%d — fetching highlights", book_id)

        try:
            result = await self._readwise.fetch(topic)
        except ReadwiseAuthError as exc:
            raise LLMRefusalError("Invalid Readwise API key") from exc
        except ReadwiseBookNotFoundError as exc:
            raise LLMRefusalError(f"Book not found in Readwise: {topic!r}") from exc
        except ReadwiseNoHighlightsError as exc:
            raise LLMRefusalError(f"No highlights found for this book") from exc

        logger.info(
            "Readwise fetch: book=%r book_id=%d highlights=%d",
            result.book_title,
            result.book_id,
            len(result.highlights),
        )

        graph = await _with_retry(
            self._llm.generate_graph_from_highlights,
            result.book_title,
            result.highlights,
        )

        graph = graph.model_copy(
            update={
                "generated_at": datetime.now(UTC),
                "resolved_title": result.book_title,
            }
        )

        # Enforce node cap
        if len(graph.nodes) > self._settings.max_nodes_per_graph:
            logger.info(
                "Trimming Readwise graph from %d to %d nodes",
                len(graph.nodes),
                self._settings.max_nodes_per_graph,
            )
            allowed_ids = {n.id for n in graph.nodes[: self._settings.max_nodes_per_graph]}
            graph = graph.model_copy(
                update={
                    "nodes": graph.nodes[: self._settings.max_nodes_per_graph],
                    "edges": [
                        e for e in graph.edges
                        if e.source in allowed_ids and e.target in allowed_ids
                    ],
                }
            )

        await self._cache.set_graph(cache_key, depth, self._settings.prompt_version, graph)
        return graph

    async def _generate_graph_web(
        self, topic: str, depth: int, force_refresh: bool
    ) -> GraphResponse:
        """Web search path (original generate_graph logic)."""
        if not force_refresh:
            cached = await self._cache.get_graph(topic, depth, self._settings.prompt_version)
            if cached is not None:
                logger.info("Cache hit for topic=%r depth=%d", topic, depth)
                return cached

        logger.info("Cache miss for topic=%r depth=%d — calling LLM", topic, depth)

        research_overview: str | None = None
        search_results: list[SearchResult] = []

        if self._research_backend is not None:
            try:
                research_overview, search_results = await self._research_backend.research(topic)
                logger.info(
                    "Research completed for topic=%r: overview_len=%d, sources=%d",
                    topic,
                    len(research_overview) if research_overview else 0,
                    len(search_results),
                )
            except Exception as exc:
                logger.error("Research failed for topic=%r: %s", topic, exc)
                raise GraphGenerationError(f"Research failed for topic {topic!r}") from exc
        elif self._search is not None:
            try:
                queries = await self._llm.generate_search_queries(topic)
                logger.info("Generated the following queries for topic=%r: %s", topic, queries)
            except Exception:
                logger.warning("Query generation failed for topic=%r, using topic as query", topic)
                queries = [topic]
            try:
                search_results = await self._search.search(queries)
                logger.info("Search returned %d results for topic=%r", len(search_results), topic)
            except Exception as exc:
                logger.error("Search failed for topic=%r: %s", topic, exc)
                raise GraphGenerationError(f"Search failed for topic {topic!r}") from exc

        graph = await _with_retry(
            self._llm.generate_graph, topic, depth, search_results, research_overview
        )

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
        # Cache key includes sorted seed labels so different neighbor contexts are cached separately
        seed_labels = ",".join(sorted(n.label.lower() for n in current_graph.nodes))
        cached = await self._cache.get_expansion(
            node_label, node_type.value, self._settings.prompt_version, seed_labels
        )
        if cached is not None:
            logger.info("Expansion cache hit for node=%r", node_label)
            addition = cached
        else:
            logger.info("Expansion cache miss for node=%r — running 4-stage pipeline", node_label)

            search_context: list[SearchResult] = []
            if self._search is not None:
                try:
                    search_context = await self._search.search([node_label])
                    logger.info("Expansion search returned %d results for node=%r", len(search_context), node_label)
                except Exception as exc:
                    raise GraphGenerationError(f"Search failed for expansion of node {node_label!r}") from exc

            # current_graph.nodes = seed nodes (origin + direct neighbors from stub_graph)
            addition = await _with_retry(
                self._llm.expand_node_pipeline,
                node_label,
                current_graph.nodes,
                context_nodes,
                search_context,
                self._settings.max_nodes_per_expand,
            )

            # Safety cap: enforce max_nodes_per_expand on truly new nodes
            seed_ids = {n.id for n in current_graph.nodes}
            new_only = [n for n in addition.nodes if n.id not in seed_ids]
            if len(new_only) > self._settings.max_nodes_per_expand:
                new_only = new_only[: self._settings.max_nodes_per_expand]
                seeds_in_addition = [n for n in addition.nodes if n.id in seed_ids]
                capped_nodes = seeds_in_addition + new_only
                capped_ids = {n.id for n in capped_nodes}
                addition = SubGraphResponse(
                    nodes=capped_nodes,
                    edges=[e for e in addition.edges if e.source in capped_ids and e.target in capped_ids],
                )

            await self._cache.set_expansion(
                node_label, node_type.value, self._settings.prompt_version, addition, seed_labels
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
