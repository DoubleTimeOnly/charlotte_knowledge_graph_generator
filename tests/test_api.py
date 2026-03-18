"""Tests for the FastAPI routes."""

from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest

from charlotte_knowledge_graph_generator.llm import GraphGenerationError, LLMRefusalError
from charlotte_knowledge_graph_generator.models import NodeType


# ── Helpers ───────────────────────────────────────────────────────────────────

_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/test")
_FAKE_RESPONSE_401 = httpx.Response(401, request=_FAKE_REQUEST)
_FAKE_RESPONSE_429 = httpx.Response(429, request=_FAKE_REQUEST)


class TestRootEndpoint:
    async def test_root_returns_html(self, api_client):
        response = await api_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


class TestHealthEndpoint:
    async def test_health_returns_ok(self, api_client):
        response = await api_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestGenerateGraphEndpoint:
    async def test_valid_topic_returns_graph(self, api_client, graph_fixture):
        response = await api_client.post(
            "/api/graph", json={"topic": "Israel-Palestine conflict", "depth": 2}
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert "topic" in data
        assert len(data["nodes"]) == len(graph_fixture.nodes)

    async def test_default_depth_accepted(self, api_client):
        response = await api_client.post(
            "/api/graph", json={"topic": "some topic"}
        )
        assert response.status_code == 200

    async def test_empty_topic_returns_422(self, api_client):
        response = await api_client.post("/api/graph", json={"topic": ""})
        assert response.status_code == 422

    async def test_invalid_depth_returns_422(self, api_client):
        response = await api_client.post(
            "/api/graph", json={"topic": "t", "depth": 0}
        )
        assert response.status_code == 422

    async def test_llm_refusal_returns_422(self, api_client, mock_llm):
        mock_llm._graph = None  # type: ignore[assignment]

        async def raise_refusal(*args, **kwargs):
            raise LLMRefusalError("refused")

        mock_llm.generate_graph = raise_refusal
        response = await api_client.post(
            "/api/graph", json={"topic": "sensitive topic"}
        )
        assert response.status_code == 422

    async def test_graph_generation_error_returns_503(self, api_client, mock_llm):
        async def raise_error(*args, **kwargs):
            raise GraphGenerationError("bad output")

        mock_llm.generate_graph = raise_error
        response = await api_client.post("/api/graph", json={"topic": "t"})
        assert response.status_code == 503

    async def test_auth_error_returns_503(self, api_client, mock_llm):
        exc = anthropic.AuthenticationError(message="bad key", response=_FAKE_RESPONSE_401, body=None)

        async def raise_auth(*args):
            raise exc

        mock_llm.generate_graph = raise_auth
        response = await api_client.post("/api/graph", json={"topic": "t"})
        assert response.status_code == 503

    async def test_timeout_returns_503(self, api_client, mock_llm):
        exc = anthropic.APITimeoutError(request=_FAKE_REQUEST)

        async def raise_timeout(*args):
            raise exc

        mock_llm.generate_graph = raise_timeout
        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            response = await api_client.post("/api/graph", json={"topic": "t"})
        assert response.status_code == 503


class TestExpandNodeEndpoint:
    async def test_valid_expand_returns_graph(self, api_client, graph_fixture):
        response = await api_client.post(
            "/api/expand",
            json={
                "node_id": "oslo_accords",
                "node_label": "Oslo Accords",
                "node_type": "Document",
                "context_nodes": ["Yasser Arafat", "PLO"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data

    async def test_missing_required_field_returns_422(self, api_client):
        response = await api_client.post(
            "/api/expand",
            json={
                "node_label": "Oslo Accords",
                "node_type": "Document",
                # node_id missing
            },
        )
        assert response.status_code == 422

    async def test_llm_refusal_returns_422(self, api_client, mock_llm):
        async def raise_refusal(*args, **kwargs):
            raise LLMRefusalError("refused")

        mock_llm.expand_node = raise_refusal
        response = await api_client.post(
            "/api/expand",
            json={
                "node_id": "n1",
                "node_label": "N1",
                "node_type": "Concept",
                "context_nodes": [],
            },
        )
        assert response.status_code == 422

    async def test_generation_error_returns_503(self, api_client, mock_llm):
        async def raise_error(*args, **kwargs):
            raise GraphGenerationError("expand failed")

        mock_llm.expand_node = raise_error
        response = await api_client.post(
            "/api/expand",
            json={"node_id": "n1", "node_label": "N1", "node_type": "Concept", "context_nodes": []},
        )
        assert response.status_code == 503

    async def test_timeout_returns_503(self, api_client, mock_llm):
        exc = anthropic.APITimeoutError(request=_FAKE_REQUEST)

        async def raise_timeout(*args, **kwargs):
            raise exc

        mock_llm.expand_node = raise_timeout
        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            response = await api_client.post(
                "/api/expand",
                json={"node_id": "n1", "node_label": "N1", "node_type": "Concept", "context_nodes": []},
            )
        assert response.status_code == 503


class TestNodeDetailEndpoint:
    async def test_valid_request_returns_detail(self, api_client, detail_fixture):
        response = await api_client.post(
            "/api/node/detail",
            json={
                "label": "Oslo Accords",
                "node_type": "Document",
                "context_nodes": ["Yasser Arafat"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["label"] == detail_fixture.label
        assert data["type"] == "Document"
        assert "summary" in data
        assert "key_facts" in data
        assert isinstance(data["key_facts"], list)

    async def test_context_nodes_optional(self, api_client):
        response = await api_client.post(
            "/api/node/detail",
            json={"label": "Oslo Accords", "node_type": "Document"},
        )
        assert response.status_code == 200

    async def test_missing_label_returns_422(self, api_client):
        response = await api_client.post(
            "/api/node/detail", json={"node_type": "Document"}
        )
        assert response.status_code == 422

    async def test_invalid_node_type_returns_422(self, api_client):
        response = await api_client.post(
            "/api/node/detail",
            json={"label": "Oslo Accords", "node_type": "InvalidType"},
        )
        assert response.status_code == 422

    async def test_llm_refusal_returns_422(self, api_client, mock_llm):
        async def raise_refusal(*args, **kwargs):
            raise LLMRefusalError("refused")

        mock_llm.get_node_detail = raise_refusal
        response = await api_client.post(
            "/api/node/detail",
            json={"label": "sensitive", "node_type": "Concept"},
        )
        assert response.status_code == 422

    async def test_generation_error_returns_503(self, api_client, mock_llm):
        async def raise_error(*args, **kwargs):
            raise GraphGenerationError("bad")

        mock_llm.get_node_detail = raise_error
        response = await api_client.post(
            "/api/node/detail",
            json={"label": "thing", "node_type": "Concept"},
        )
        assert response.status_code == 503

    async def test_timeout_returns_503(self, api_client, mock_llm):
        exc = anthropic.APITimeoutError(request=_FAKE_REQUEST)

        async def raise_timeout(*args, **kwargs):
            raise exc

        mock_llm.get_node_detail = raise_timeout
        with patch("charlotte_knowledge_graph_generator.graph_service.asyncio.sleep", new_callable=AsyncMock):
            response = await api_client.post(
                "/api/node/detail",
                json={"label": "thing", "node_type": "Concept"},
            )
        assert response.status_code == 503


class TestCacheStatsEndpoint:
    async def test_cache_stats_returns_ok(self, api_client):
        response = await api_client.get("/admin/cache/stats")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
