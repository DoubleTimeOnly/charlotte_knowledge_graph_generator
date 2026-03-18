"""Tests for GraphService business logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from charlotte_knowledge_graph_generator.graph_service import GraphService, _merge_graphs, _with_retry
from charlotte_knowledge_graph_generator.models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    NodeType,
    SearchResult,
    SubGraphResponse,
)
from charlotte_knowledge_graph_generator.search import SearchService


# ── _with_retry ───────────────────────────────────────────────────────────────


class TestWithRetry:
    async def test_raises_after_exhausting_timeout_retries(self):
        request = httpx.Request("POST", "https://api.anthropic.com/test")
        exc = anthropic.APITimeoutError(request=request)

        async def always_timeout(*args):
            raise exc

        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(anthropic.APITimeoutError):
                await _with_retry(always_timeout)

    async def test_retries_multiple_times_before_raising_timeout(self):
        request = httpx.Request("POST", "https://api.anthropic.com/test")
        exc = anthropic.APITimeoutError(request=request)
        call_count = 0

        async def count_and_timeout(*args):
            nonlocal call_count
            call_count += 1
            raise exc

        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(anthropic.APITimeoutError):
                await _with_retry(count_and_timeout)

        # MAX_RETRIES=2 means 3 total attempts (0, 1, 2)
        assert call_count == 3

    async def test_raises_after_exhausting_rate_limit_retries(self):
        request = httpx.Request("POST", "https://api.anthropic.com/test")
        response = httpx.Response(429, request=request)
        exc = anthropic.RateLimitError(message="rate limited", response=response, body=None)

        async def always_rate_limited(*args):
            raise exc

        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(anthropic.RateLimitError):
                await _with_retry(always_rate_limited)

    async def test_succeeds_on_first_call_with_no_retries(self):
        async def succeed(*args):
            return "result"

        result = await _with_retry(succeed)
        assert result == "result"


# ── _merge_graphs ─────────────────────────────────────────────────────────────


class TestMergeGraphs:
    def _node(self, node_id: str, label: str = "") -> GraphNode:
        return GraphNode(
            id=node_id,
            label=label or node_id,
            type=NodeType.CONCEPT,
            description="test",
        )

    def _edge(self, source: str, target: str) -> GraphEdge:
        return GraphEdge(source=source, target=target, relationship_type="related")

    def test_adds_new_nodes(self):
        base = GraphResponse(
            nodes=[self._node("a"), self._node("b")],
            edges=[],
            topic="t",
        )
        addition = SubGraphResponse(
            nodes=[self._node("c"), self._node("d")],
            edges=[],
        )
        merged = _merge_graphs(base, addition)
        assert {n.id for n in merged.nodes} == {"a", "b", "c", "d"}

    def test_deduplicates_existing_nodes(self):
        base = GraphResponse(
            nodes=[self._node("a"), self._node("b")],
            edges=[],
            topic="t",
        )
        addition = SubGraphResponse(
            nodes=[self._node("b"), self._node("c")],  # "b" already in base
            edges=[],
        )
        merged = _merge_graphs(base, addition)
        ids = [n.id for n in merged.nodes]
        assert ids.count("b") == 1
        assert len(merged.nodes) == 3

    def test_includes_edges_spanning_new_and_existing_nodes(self):
        base = GraphResponse(
            nodes=[self._node("a")],
            edges=[],
            topic="t",
        )
        addition = SubGraphResponse(
            nodes=[self._node("b")],
            edges=[self._edge("a", "b")],  # a=existing, b=new
        )
        merged = _merge_graphs(base, addition)
        assert len(merged.edges) == 1

    def test_drops_edges_with_missing_endpoint(self):
        base = GraphResponse(nodes=[self._node("a")], edges=[], topic="t")
        addition = SubGraphResponse(
            nodes=[self._node("b")],
            edges=[
                self._edge("b", "ghost"),  # "ghost" not in any graph
            ],
        )
        merged = _merge_graphs(base, addition)
        assert len(merged.edges) == 0

    def test_preserves_base_edges(self):
        base = GraphResponse(
            nodes=[self._node("a"), self._node("b")],
            edges=[self._edge("a", "b")],
            topic="t",
        )
        addition = SubGraphResponse(nodes=[self._node("c")], edges=[])
        merged = _merge_graphs(base, addition)
        assert len(merged.edges) == 1

    def test_preserves_topic(self):
        base = GraphResponse(nodes=[], edges=[], topic="Israel-Palestine conflict")
        addition = SubGraphResponse(nodes=[], edges=[])
        merged = _merge_graphs(base, addition)
        assert merged.topic == "Israel-Palestine conflict"


# ── GraphService ──────────────────────────────────────────────────────────────


class TestGenerateGraph:
    async def test_cache_miss_calls_llm(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        result = await service.generate_graph("Israel-Palestine conflict", depth=2)
        assert mock_llm.generate_graph_calls == 1
        assert result.topic == graph_fixture.topic
        assert len(result.nodes) == len(graph_fixture.nodes)

    async def test_cache_hit_skips_llm(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        # First call populates cache
        await service.generate_graph("Israel-Palestine conflict", depth=2)
        assert mock_llm.generate_graph_calls == 1
        # Second call should hit cache
        await service.generate_graph("Israel-Palestine conflict", depth=2)
        assert mock_llm.generate_graph_calls == 1  # not incremented

    async def test_result_stored_in_cache(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        await service.generate_graph("Israel-Palestine conflict", depth=2)
        cached = await cache.get_graph(
            "Israel-Palestine conflict", 2, test_settings.prompt_version
        )
        assert cached is not None
        assert cached.topic == graph_fixture.topic

    async def test_trims_excess_nodes(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        # Set a node cap smaller than the fixture's node count
        test_settings.max_nodes_per_graph = 3
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        result = await service.generate_graph("t", depth=1)
        assert len(result.nodes) <= 3
        # All edges must reference only kept nodes
        kept_ids = {n.id for n in result.nodes}
        for edge in result.edges:
            assert edge.source in kept_ids
            assert edge.target in kept_ids

    async def test_different_depths_produce_separate_cache_entries(
        self, mock_llm, cache, test_settings
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        await service.generate_graph("topic", depth=1)
        await service.generate_graph("topic", depth=2)
        assert mock_llm.generate_graph_calls == 2


class TestExpandNode:
    async def test_expand_calls_llm(
        self, mock_llm, cache, test_settings, graph_fixture, subgraph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        merged = await service.expand_node(
            node_label="Oslo Accords",
            node_type=NodeType.DOCUMENT,
            context_nodes=["Yasser Arafat", "PLO"],
            current_graph=graph_fixture,
        )
        assert mock_llm.expand_node_calls == 1
        # New node from subgraph_fixture should appear
        node_ids = {n.id for n in merged.nodes}
        assert "jimmy_carter" in node_ids

    async def test_expand_merges_without_duplicating_existing_nodes(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        original_count = len(graph_fixture.nodes)
        merged = await service.expand_node(
            node_label="Oslo Accords",
            node_type=NodeType.DOCUMENT,
            context_nodes=[],
            current_graph=graph_fixture,
        )
        # Should have original nodes + 1 new node (jimmy_carter)
        assert len(merged.nodes) == original_count + 1

    async def test_expand_cap_enforced(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        # Provide a subgraph with 2 nodes so the cap branch is exercised
        multi_subgraph = SubGraphResponse(
            nodes=[
                GraphNode(id="node_a", label="Node A", type=NodeType.CONCEPT, description="a"),
                GraphNode(id="node_b", label="Node B", type=NodeType.CONCEPT, description="b"),
            ],
            edges=[],
        )
        mock_llm._subgraph = multi_subgraph

        async def return_multi(*args, **kwargs):
            return multi_subgraph

        mock_llm.expand_node = return_multi

        test_settings.max_nodes_per_expand = 1  # only 1 new node allowed
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        merged = await service.expand_node(
            node_label="Oslo Accords",
            node_type=NodeType.DOCUMENT,
            context_nodes=[],
            current_graph=graph_fixture,
        )
        new_node_count = len(merged.nodes) - len(graph_fixture.nodes)
        assert new_node_count <= 1


class TestGetNodeDetail:
    async def test_cache_miss_calls_llm(
        self, mock_llm, cache, test_settings, detail_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        detail = await service.get_node_detail(
            label="Oslo Accords",
            node_type=NodeType.DOCUMENT,
            context_nodes=[],
        )
        assert mock_llm.get_node_detail_calls == 1
        assert detail.label == detail_fixture.label

    async def test_cache_hit_skips_llm(
        self, mock_llm, cache, test_settings
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        await service.get_node_detail("Oslo Accords", NodeType.DOCUMENT, [])
        assert mock_llm.get_node_detail_calls == 1
        await service.get_node_detail("Oslo Accords", NodeType.DOCUMENT, [])
        assert mock_llm.get_node_detail_calls == 1  # not incremented

    async def test_result_stored_in_cache(
        self, mock_llm, cache, test_settings, detail_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        await service.get_node_detail("Oslo Accords", NodeType.DOCUMENT, [])
        cached = await cache.get_node_detail(
            "Oslo Accords", NodeType.DOCUMENT.value, test_settings.prompt_version
        )
        assert cached is not None
        assert cached.label == detail_fixture.label


# ── GraphService + SearchService integration ──────────────────────────────────


def _make_search_service_mock(results: list[SearchResult]) -> AsyncMock:
    """Build a mock SearchService whose search() returns given results."""
    mock = MagicMock(spec=SearchService)
    mock.search = AsyncMock(return_value=results)
    return mock


class TestGenerateGraphWithSearch:
    async def test_search_enabled_passes_results_to_llm(
        self, mock_llm, cache, test_settings
    ):
        search_results = [SearchResult(title="T", url="https://example.com", snippet="s")]
        search_mock = _make_search_service_mock(search_results)
        service = GraphService(
            llm=mock_llm, cache=cache, settings=test_settings, search=search_mock
        )
        await service.generate_graph("topic", depth=2)
        assert mock_llm.last_search_context == search_results
        search_mock.search.assert_called_once()

    async def test_search_disabled_llm_called_with_empty_context(
        self, mock_llm, cache, test_settings
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings, search=None)
        await service.generate_graph("topic", depth=2)
        assert mock_llm.last_search_context == []

    async def test_search_failure_falls_back_to_llm_only(
        self, mock_llm, cache, test_settings
    ):
        """If SearchService.search() raises, generate_graph still succeeds."""
        search_mock = MagicMock(spec=SearchService)
        search_mock.search = AsyncMock(side_effect=Exception("Tavily down"))
        service = GraphService(
            llm=mock_llm, cache=cache, settings=test_settings, search=search_mock
        )
        # Should not raise — graceful fallback
        result = await service.generate_graph("topic", depth=2)
        assert result is not None
        assert mock_llm.generate_graph_calls == 1

    async def test_query_gen_failure_falls_back_to_topic(
        self, mock_llm, cache, test_settings
    ):
        """If generate_search_queries raises, falls back to [topic] and continues."""
        search_results = [SearchResult(title="T", url="https://example.com", snippet="s")]
        search_mock = _make_search_service_mock(search_results)

        mock_llm.generate_search_queries = AsyncMock(side_effect=Exception("LLM down"))

        service = GraphService(
            llm=mock_llm, cache=cache, settings=test_settings, search=search_mock
        )
        result = await service.generate_graph("my topic", depth=2)
        assert result is not None
        # Search was still called — with [topic] as fallback query
        search_mock.search.assert_called_once_with(["my topic"])

    async def test_force_refresh_bypasses_cache_read(
        self, mock_llm, cache, test_settings, graph_fixture
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        # Populate cache first
        await service.generate_graph("topic", depth=2)
        assert mock_llm.generate_graph_calls == 1

        # force_refresh=True should bypass cache read
        await service.generate_graph("topic", depth=2, force_refresh=True)
        assert mock_llm.generate_graph_calls == 2

    async def test_force_refresh_writes_to_cache(
        self, mock_llm, cache, test_settings
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        await service.generate_graph("topic", depth=2, force_refresh=True)
        # Result should be in cache after force_refresh
        cached = await cache.get_graph("topic", 2, test_settings.prompt_version)
        assert cached is not None

    async def test_generated_at_is_set_on_new_graph(
        self, mock_llm, cache, test_settings
    ):
        service = GraphService(llm=mock_llm, cache=cache, settings=test_settings)
        result = await service.generate_graph("topic", depth=2)
        assert result.generated_at is not None

    async def test_partial_search_failure_uses_available_results(
        self, mock_llm, cache, test_settings
    ):
        """If one of 2 search queries fails, partial results still used."""
        partial_results = [SearchResult(title="T", url="https://example.com/1", snippet="s")]

        search_mock = MagicMock(spec=SearchService)
        # search() handles partial failures internally and returns what it can
        search_mock.search = AsyncMock(return_value=partial_results)

        service = GraphService(
            llm=mock_llm, cache=cache, settings=test_settings, search=search_mock
        )
        result = await service.generate_graph("topic", depth=2)
        assert mock_llm.last_search_context == partial_results
