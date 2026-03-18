"""Shared pytest fixtures and mock implementations.

Must set ANTHROPIC_API_KEY before any package import so pydantic-settings
can construct Settings() at module level.
"""

import json
import os
from pathlib import Path

# ── Must come before any package imports ──────────────────────────────────────
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key-for-tests")

import pytest
from httpx import ASGITransport, AsyncClient

from charlotte_knowledge_graph_generator.cache import CacheLayer
from charlotte_knowledge_graph_generator.config import Settings
from charlotte_knowledge_graph_generator.models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    NodeDetail,
    NodeType,
    SearchResult,
    SubGraphResponse,
)

# ── Fixture file helpers ───────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


# ── Mock LLM client ────────────────────────────────────────────────────────────


class MockLLMClient:
    """In-process LLM stub — never calls the Anthropic API."""

    def __init__(
        self,
        graph: GraphResponse,
        subgraph: SubGraphResponse,
        detail: NodeDetail,
    ) -> None:
        self._graph = graph
        self._subgraph = subgraph
        self._detail = detail
        self.generate_graph_calls: int = 0
        self.generate_search_queries_calls: int = 0
        self.expand_node_calls: int = 0
        self.get_node_detail_calls: int = 0
        self.last_search_context: list[SearchResult] = []

    async def generate_search_queries(self, topic: str) -> list[str]:
        self.generate_search_queries_calls += 1
        return ["test query 1", "test query 2"]

    async def generate_graph(
        self,
        topic: str,
        depth: int,
        search_context: list[SearchResult] | None = None,
    ) -> GraphResponse:
        self.generate_graph_calls += 1
        self.last_search_context = search_context or []
        return self._graph

    async def expand_node(
        self,
        node_label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> SubGraphResponse:
        self.expand_node_calls += 1
        return self._subgraph

    async def get_node_detail(
        self,
        label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> NodeDetail:
        self.get_node_detail_calls += 1
        return self._detail


# ── Pytest fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def graph_fixture() -> GraphResponse:
    return GraphResponse.model_validate(_load("israel_palestine_graph.json"))


@pytest.fixture
def subgraph_fixture() -> SubGraphResponse:
    """A small expansion subgraph with a node not already in graph_fixture."""
    return SubGraphResponse(
        nodes=[
            GraphNode(
                id="jimmy_carter",
                label="Jimmy Carter",
                type=NodeType.PERSON,
                description="US President who mediated the Camp David Accords.",
            )
        ],
        edges=[
            GraphEdge(
                source="jimmy_carter",
                target="oslo_accords",
                relationship_type="preceded",
                weight=3,
            )
        ],
    )


@pytest.fixture
def detail_fixture() -> NodeDetail:
    return NodeDetail.model_validate(_load("node_detail_oslo_accords.json"))


@pytest.fixture
def mock_llm(
    graph_fixture: GraphResponse,
    subgraph_fixture: SubGraphResponse,
    detail_fixture: NodeDetail,
) -> MockLLMClient:
    return MockLLMClient(
        graph=graph_fixture,
        subgraph=subgraph_fixture,
        detail=detail_fixture,
    )


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        anthropic_api_key="sk-ant-test-key",
        cache_db_path=str(tmp_path / "test_cache.db"),
        max_nodes_per_graph=25,
        max_nodes_per_expand=12,
        rate_limit_per_minute=100,
        static_dir="static",
        prompt_version="v1",
    )


@pytest.fixture
async def cache(test_settings: Settings) -> CacheLayer:
    c = CacheLayer(test_settings.cache_db_path)
    await c.setup()
    yield c
    await c.close()


@pytest.fixture
async def api_client(mock_llm: MockLLMClient, test_settings: Settings, tmp_path):
    """AsyncClient wired to the FastAPI app with mocked dependencies."""
    from charlotte_knowledge_graph_generator.api import app, get_service
    from charlotte_knowledge_graph_generator.graph_service import GraphService

    # Build a real GraphService backed by mock LLM + temp cache
    real_cache = CacheLayer(str(tmp_path / "api_test_cache.db"))
    await real_cache.setup()

    service = GraphService(llm=mock_llm, cache=real_cache, settings=test_settings)
    app.dependency_overrides[get_service] = lambda: service
    app.state.cache = real_cache

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await real_cache.close()
