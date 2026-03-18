"""Unit tests for AnthropicLLMClient private stage methods.

Mocks anthropic.AsyncAnthropic at the SDK level — never hits the real API.

Stage pipeline:
  _survey_entities  → list[_LLMNodeInput]
  _construct_edges  → list[_LLMEdgeInput]
  _validate_graph   → list[_LLMValidationIssue]
  _enrich_graph     → GraphResponse
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from charlotte_knowledge_graph_generator.llm import (
    AnthropicLLMClient,
    GraphGenerationError,
    LLMRefusalError,
)
from charlotte_knowledge_graph_generator.models import NodeType


# ── SDK response builder ───────────────────────────────────────────────────────

def _make_tool_response(tool_name: str, payload: dict) -> MagicMock:
    """Build a fake anthropic.types.Message with a single tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = payload

    msg = MagicMock()
    msg.content = [block]
    msg.stop_reason = "tool_use"
    return msg


def _make_no_tool_response() -> MagicMock:
    """Build a fake anthropic.types.Message with no tool_use block (refusal)."""
    block = MagicMock()
    block.type = "text"
    block.text = "I'm sorry, I can't help with that."

    msg = MagicMock()
    msg.content = [block]
    msg.stop_reason = "end_turn"
    return msg


def _make_client(response: MagicMock) -> AnthropicLLMClient:
    """Return a client whose SDK create() always returns `response`."""
    sdk = MagicMock()
    sdk.messages = MagicMock()
    sdk.messages.create = AsyncMock(return_value=response)
    return AnthropicLLMClient(client=sdk, model="claude-test")


# ── Sample payloads ────────────────────────────────────────────────────────────

_SAMPLE_NODE = {
    "label": "Oslo Accords",
    "type": "Document",
    "description": "The Oslo Accords were a pair of agreements signed in 1993 and 1995.",
    "era": "1993–1995",
}

_SAMPLE_EDGE = {
    "source_label": "Yasser Arafat",
    "target_label": "Oslo Accords",
    "relationship_type": "signed",
    "weight": 4,
}

_SAMPLE_ISSUE = {
    "severity": "high",
    "description": "Node 'X' has no incoming or outgoing edges.",
}

_SURVEY_PAYLOAD = {"nodes": [_SAMPLE_NODE] * 5}

_EDGE_PAYLOAD = {"edges": [_SAMPLE_EDGE] * 6}  # ≥5 required

_VALIDATE_PAYLOAD = {"issues": [_SAMPLE_ISSUE]}

_ENRICH_PAYLOAD = {
    "nodes": [_SAMPLE_NODE],
    "edges": [_SAMPLE_EDGE],
}


# ── _survey_entities ──────────────────────────────────────────────────────────


class TestSurveyEntities:
    async def test_happy_path_returns_node_list(self):
        client = _make_client(_make_tool_response("create_node_list", _SURVEY_PAYLOAD))
        nodes = await client._survey_entities("Israel-Palestine conflict")
        assert len(nodes) == 5
        assert nodes[0].label == "Oslo Accords"
        assert nodes[0].type == NodeType.DOCUMENT

    async def test_refusal_raises_llm_refusal_error(self):
        client = _make_client(_make_no_tool_response())
        with pytest.raises(LLMRefusalError):
            await client._survey_entities("sensitive topic")

    async def test_invalid_schema_raises_graph_generation_error(self):
        # Missing required 'type' field
        bad_payload = {"nodes": [{"label": "X", "description": "d"}]}
        client = _make_client(_make_tool_response("create_node_list", bad_payload))
        with pytest.raises(GraphGenerationError, match="SURVEY stage"):
            await client._survey_entities("topic")


# ── _construct_edges ──────────────────────────────────────────────────────────


class TestConstructEdges:
    def _nodes(self):
        from charlotte_knowledge_graph_generator.models import _LLMNodeInput
        return [
            _LLMNodeInput(
                label="Yasser Arafat",
                type=NodeType.PERSON,
                description="Palestinian leader.",
            ),
            _LLMNodeInput(
                label="Oslo Accords",
                type=NodeType.DOCUMENT,
                description="1993 peace agreement.",
                era="1993",
            ),
        ]

    async def test_happy_path_returns_edge_list(self):
        client = _make_client(_make_tool_response("create_edge_list", _EDGE_PAYLOAD))
        edges = await client._construct_edges("topic", self._nodes())
        assert len(edges) == 6
        assert edges[0].source_label == "Yasser Arafat"
        assert edges[0].relationship_type == "signed"

    async def test_refusal_raises_llm_refusal_error(self):
        client = _make_client(_make_no_tool_response())
        with pytest.raises(LLMRefusalError):
            await client._construct_edges("topic", self._nodes())

    async def test_too_few_edges_raises_graph_generation_error(self):
        # Only 3 edges — below the minimum of 5
        too_few = {"edges": [_SAMPLE_EDGE] * 3}
        client = _make_client(_make_tool_response("create_edge_list", too_few))
        with pytest.raises(GraphGenerationError, match="too few edges|minimum 5"):
            await client._construct_edges("topic", self._nodes())

    async def test_invalid_schema_raises_graph_generation_error(self):
        bad_payload = {"edges": [{"source_label": "X"}]}  # missing required fields
        client = _make_client(_make_tool_response("create_edge_list", bad_payload))
        with pytest.raises(GraphGenerationError, match="EDGES stage"):
            await client._construct_edges("topic", self._nodes())


# ── _validate_graph ───────────────────────────────────────────────────────────


class TestValidateGraph:
    def _nodes(self):
        from charlotte_knowledge_graph_generator.models import _LLMNodeInput
        return [
            _LLMNodeInput(
                label="Oslo Accords", type=NodeType.DOCUMENT, description="d", era="1993"
            )
        ]

    def _edges(self):
        from charlotte_knowledge_graph_generator.models import _LLMEdgeInput
        return [
            _LLMEdgeInput(
                source_label="Yasser Arafat",
                target_label="Oslo Accords",
                relationship_type="signed",
            )
        ]

    async def test_happy_path_returns_issues(self):
        client = _make_client(_make_tool_response("validate_graph", _VALIDATE_PAYLOAD))
        issues = await client._validate_graph(self._nodes(), self._edges())
        assert len(issues) == 1
        assert issues[0].severity == "high"

    async def test_empty_issues_list_is_valid(self):
        """Empty issues list means the graph is clean — should not raise."""
        client = _make_client(_make_tool_response("validate_graph", {"issues": []}))
        issues = await client._validate_graph(self._nodes(), self._edges())
        assert issues == []

    async def test_refusal_raises_llm_refusal_error(self):
        client = _make_client(_make_no_tool_response())
        with pytest.raises(LLMRefusalError):
            await client._validate_graph(self._nodes(), self._edges())

    async def test_invalid_schema_raises_graph_generation_error(self):
        # 'issues' key missing entirely
        client = _make_client(_make_tool_response("validate_graph", {"wrong": "key"}))
        with pytest.raises(GraphGenerationError, match="VALIDATE stage"):
            await client._validate_graph(self._nodes(), self._edges())


# ── _enrich_graph ─────────────────────────────────────────────────────────────


class TestEnrichGraph:
    def _nodes(self):
        from charlotte_knowledge_graph_generator.models import _LLMNodeInput
        return [
            _LLMNodeInput(
                label="Oslo Accords", type=NodeType.DOCUMENT, description="d", era="1993"
            )
        ]

    def _edges(self):
        from charlotte_knowledge_graph_generator.models import _LLMEdgeInput
        return [
            _LLMEdgeInput(
                source_label="Yasser Arafat",
                target_label="Oslo Accords",
                relationship_type="signed",
            )
        ]

    def _issues(self):
        from charlotte_knowledge_graph_generator.models import _LLMValidationIssue
        return [_LLMValidationIssue(severity="high", description="Fix this.")]

    async def test_happy_path_returns_graph_response(self):
        client = _make_client(_make_tool_response("create_knowledge_graph", _ENRICH_PAYLOAD))
        result = await client._enrich_graph(
            "Israel-Palestine conflict", self._nodes(), self._edges(), self._issues()
        )
        assert result.topic == "Israel-Palestine conflict"
        assert len(result.nodes) == 1
        assert result.nodes[0].label == "Oslo Accords"

    async def test_empty_issues_list_still_produces_graph(self):
        client = _make_client(_make_tool_response("create_knowledge_graph", _ENRICH_PAYLOAD))
        result = await client._enrich_graph("topic", self._nodes(), self._edges(), [])
        assert result.topic == "topic"
        assert len(result.nodes) == 1

    async def test_refusal_raises_llm_refusal_error(self):
        client = _make_client(_make_no_tool_response())
        with pytest.raises(LLMRefusalError):
            await client._enrich_graph("topic", self._nodes(), self._edges(), [])

    async def test_invalid_schema_raises_graph_generation_error(self):
        bad_payload = {"nodes": [], "edges": "not-a-list"}
        client = _make_client(_make_tool_response("create_knowledge_graph", bad_payload))
        with pytest.raises(GraphGenerationError, match="ENRICH stage"):
            await client._enrich_graph("topic", self._nodes(), self._edges(), [])
