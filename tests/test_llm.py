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
    _process_llm_nodes,
    _resolve_source_urls,
)
from charlotte_knowledge_graph_generator.models import NodeType, SearchResult


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

    async def test_research_overview_injected_into_prompt(self):
        """When research_overview is provided, [RESEARCH_OVERVIEW] block appears in the user prompt."""
        sdk = MagicMock()
        sdk.messages = MagicMock()
        sdk.messages.create = AsyncMock(
            return_value=_make_tool_response("create_node_list", _SURVEY_PAYLOAD)
        )
        client = AnthropicLLMClient(client=sdk, model="claude-test")
        overview = "This is the synthesized research overview."

        await client._survey_entities("some topic", research_overview=overview)

        call_kwargs = sdk.messages.create.call_args.kwargs
        user_message = next(m["content"] for m in call_kwargs["messages"] if m["role"] == "user")
        assert "[RESEARCH_OVERVIEW]" in user_message
        assert overview in user_message


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

    async def test_source_indices_resolved_to_urls(self):
        """Nodes with source_indices in ENRICH payload get source_urls populated."""
        search_results = [
            SearchResult(title="T1", url="https://example.com/1", snippet="s1"),
            SearchResult(title="T2", url="https://example.com/2", snippet="s2"),
        ]
        enriched_node = {**_SAMPLE_NODE, "source_indices": [1, 2]}  # 1-based
        payload = {"nodes": [enriched_node], "edges": [_SAMPLE_EDGE]}
        client = _make_client(_make_tool_response("create_knowledge_graph", payload))
        result = await client._enrich_graph(
            "topic", self._nodes(), self._edges(), [], search_results
        )
        assert result.nodes[0].source_urls == [
            "https://example.com/1",
            "https://example.com/2",
        ]

    async def test_source_indices_empty_when_no_search_results(self):
        """When search_context is empty, source_urls should be empty."""
        enriched_node = {**_SAMPLE_NODE, "source_indices": [1]}  # 1-based
        payload = {"nodes": [enriched_node], "edges": [_SAMPLE_EDGE]}
        client = _make_client(_make_tool_response("create_knowledge_graph", payload))
        result = await client._enrich_graph("topic", self._nodes(), self._edges(), [])
        assert result.nodes[0].source_urls == []


# ── _survey_expansion ─────────────────────────────────────────────────────────


# Minimal survey output: one new entity
_EXPANSION_SURVEY_PAYLOAD = {"nodes": [_SAMPLE_NODE]}


class TestExpansionSurvey:
    async def test_survey_expansion_includes_source_context_in_prompt(self):
        """search_context is formatted and injected into the [SOURCE_CONTEXT] block."""
        from charlotte_knowledge_graph_generator.models import GraphNode
        search_results = [
            SearchResult(title="T1", url="https://example.com/1", snippet="unique snippet text")
        ]
        sdk = MagicMock()
        sdk.messages = MagicMock()
        sdk.messages.create = AsyncMock(
            return_value=_make_tool_response("create_expansion_entities", _EXPANSION_SURVEY_PAYLOAD)
        )
        client = AnthropicLLMClient(client=sdk, model="claude-test")
        seed = [GraphNode(id="n1", label="Seed Node", type=NodeType.CONCEPT, description="seed")]
        await client._survey_expansion(
            node_label="Oslo Accords",
            seed_nodes=seed,
            context_nodes=[],
            search_context=search_results,
        )
        call_kwargs = sdk.messages.create.call_args.kwargs
        user_message = next(m["content"] for m in call_kwargs["messages"] if m["role"] == "user")
        assert "[SOURCE_CONTEXT]" in user_message
        assert "unique snippet text" in user_message

    async def test_survey_expansion_includes_seed_node_names(self):
        """Seed nodes are listed in the prompt so the LLM doesn't re-generate them."""
        from charlotte_knowledge_graph_generator.models import GraphNode
        sdk = MagicMock()
        sdk.messages = MagicMock()
        sdk.messages.create = AsyncMock(
            return_value=_make_tool_response("create_expansion_entities", _EXPANSION_SURVEY_PAYLOAD)
        )
        client = AnthropicLLMClient(client=sdk, model="claude-test")
        seed = [GraphNode(id="n1", label="Yasser Arafat", type=NodeType.PERSON, description="PLO leader")]
        await client._survey_expansion(
            node_label="Oslo Accords",
            seed_nodes=seed,
            context_nodes=[],
        )
        call_kwargs = sdk.messages.create.call_args.kwargs
        user_message = next(m["content"] for m in call_kwargs["messages"] if m["role"] == "user")
        assert "Yasser Arafat" in user_message


# ── generate_search_queries ───────────────────────────────────────────────────


def _make_text_response(text: str) -> MagicMock:
    """Build a fake anthropic.types.Message with a text block (no tool use)."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    msg = MagicMock()
    msg.content = [block]
    msg.stop_reason = "end_turn"
    return msg


class TestGenerateSearchQueries:
    async def test_returns_topic_as_single_query(self):
        """Stub implementation returns [topic] directly without calling the LLM."""
        client = _make_client(_make_text_response(""))
        queries = await client.generate_search_queries("Israel-Palestine conflict")
        assert queries == ["Israel-Palestine conflict"]

    async def test_returns_topic_verbatim_for_any_input(self):
        """Result is always [topic] regardless of topic content."""
        client = _make_client(_make_text_response(""))
        queries = await client.generate_search_queries("my topic")
        assert queries == ["my topic"]

    async def test_does_not_call_llm(self):
        """Stub bypasses LLM — no API call should be made."""
        sdk = MagicMock()
        sdk.messages = MagicMock()
        sdk.messages.create = AsyncMock()
        client = AnthropicLLMClient(client=sdk, model="claude-test")
        await client.generate_search_queries("topic")
        sdk.messages.create.assert_not_called()


# ── _resolve_source_urls ──────────────────────────────────────────────────────


class TestResolveSourceUrls:
    def _results(self):
        return [
            SearchResult(title="T0", url="https://example.com/0", snippet="s0"),
            SearchResult(title="T1", url="https://example.com/1", snippet="s1"),
            SearchResult(title="T2", url="https://example.com/2", snippet="s2"),
        ]

    def test_valid_indices_return_urls(self):
        urls = _resolve_source_urls([1, 3], self._results())  # 1-based: [1]→/0, [3]→/2
        assert urls == ["https://example.com/0", "https://example.com/2"]

    def test_out_of_range_indices_filtered(self):
        urls = _resolve_source_urls([99], self._results())
        assert urls == []

    def test_non_http_url_filtered(self):
        results = [SearchResult(title="Bad", url="javascript:evil()", snippet="xss")]
        urls = _resolve_source_urls([1], results)  # 1-based
        assert urls == []

    def test_empty_indices_returns_empty(self):
        urls = _resolve_source_urls([], self._results())
        assert urls == []

    def test_empty_results_returns_empty(self):
        urls = _resolve_source_urls([1, 2], [])
        assert urls == []

    def test_zero_index_filtered(self):
        """0 is out of range for 1-based indexing."""
        urls = _resolve_source_urls([0], self._results())
        assert urls == []


# ── _process_llm_nodes ────────────────────────────────────────────────────────


class TestProcessLlmNodes:
    def _make_node(self, label: str, source_indices: list[int] | None = None):
        from charlotte_knowledge_graph_generator.models import _LLMEnrichedNodeInput

        kwargs = {"label": label, "type": NodeType.CONCEPT, "description": "desc"}
        if source_indices is not None:
            kwargs["source_indices"] = source_indices
        return _LLMEnrichedNodeInput(**kwargs)

    def test_deduplicates_nodes_by_canonical_id(self):
        nodes = [self._make_node("Oslo Accords"), self._make_node("Oslo Accords")]
        result_nodes, _ = _process_llm_nodes(nodes)
        assert len(result_nodes) == 1

    def test_resolves_source_indices_to_urls(self):
        search_results = [
            SearchResult(title="T0", url="https://example.com/0", snippet="s0"),
        ]
        nodes = [self._make_node("Oslo Accords", source_indices=[1])]  # 1-based
        result_nodes, _ = _process_llm_nodes(nodes, search_results)
        assert result_nodes[0].source_urls == ["https://example.com/0"]

    def test_no_source_indices_gives_empty_source_urls(self):
        nodes = [self._make_node("Oslo Accords")]
        result_nodes, _ = _process_llm_nodes(nodes)
        assert result_nodes[0].source_urls == []

    def test_builds_label_to_id_map(self):
        nodes = [self._make_node("Oslo Accords"), self._make_node("Yasser Arafat")]
        _, label_to_id = _process_llm_nodes(nodes)
        assert "oslo accords" in label_to_id
        assert "yasser arafat" in label_to_id
