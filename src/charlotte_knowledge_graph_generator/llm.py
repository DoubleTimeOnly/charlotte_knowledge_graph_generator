"""LLM client — Anthropic SDK wrapper with typed tool-use output.

Architecture:
  LLMClientProtocol  (typing.Protocol)
       │
       └── AnthropicLLMClient  (production)
       └── MockLLMClient       (tests — lives in tests/conftest.py)

GraphService receives the client via constructor injection so tests never
hit the real API.
"""

import logging
from typing import Protocol, runtime_checkable

import anthropic
from pydantic import ValidationError

from charlotte_knowledge_graph_generator.models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    NodeDetail,
    NodeType,
    SubGraphResponse,
    _LLMGraphInput,
    _LLMNodeDetailInput,
    _LLMNodeInput,
    _LLMSubGraphInput,
)
from charlotte_knowledge_graph_generator.prompts import (
    EXPAND_SYSTEM,
    EXPAND_USER,
    GRAPH_SYSTEM,
    GRAPH_USER,
    NODE_DETAIL_SYSTEM,
    NODE_DETAIL_USER,
)

logger = logging.getLogger(__name__)


class LLMRefusalError(Exception):
    """Raised when the LLM does not call the expected tool (e.g. safety refusal)."""


class GraphGenerationError(Exception):
    """Raised when LLM output fails Pydantic validation."""


def _canonical_id(label: str) -> str:
    """Deterministic node ID derived from label. Used for deduplication."""
    return label.lower().strip().replace(" ", "_").replace("-", "_")


def _process_llm_graph(raw: _LLMGraphInput, topic: str) -> GraphResponse:
    """Convert LLM tool output (label-based) to a validated GraphResponse (ID-based).

    LLM tool input flow:
      _LLMGraphInput (labels) ──► deduplicate nodes ──► build label→id map
                                ──► convert edge labels to IDs ──► GraphResponse
    """
    seen_ids: set[str] = set()
    nodes: list[GraphNode] = []
    label_to_id: dict[str, str] = {}

    for n in raw.nodes:
        node_id = _canonical_id(n.label)
        if node_id in seen_ids:
            logger.warning("Duplicate node label from LLM: %s — skipping", n.label)
            continue
        seen_ids.add(node_id)
        label_to_id[n.label.lower().strip()] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                label=n.label,
                type=n.type,
                description=n.description,
                era=n.era,
            )
        )

    edges = []
    for e in raw.edges:
        source_id = label_to_id.get(e.source_label.lower().strip())
        target_id = label_to_id.get(e.target_label.lower().strip())
        if not source_id or not target_id or source_id == target_id:
            logger.debug(
                "Skipping invalid edge: %s → %s", e.source_label, e.target_label
            )
            continue
        edges.append(
            GraphEdge(
                source=source_id,
                target=target_id,
                relationship_type=e.relationship_type,
                weight=e.weight,
            )
        )

    return GraphResponse(nodes=nodes, edges=edges, topic=topic)


def _process_llm_subgraph(raw: _LLMSubGraphInput) -> SubGraphResponse:
    """Convert LLM expansion output to a SubGraphResponse."""
    seen_ids: set[str] = set()
    nodes: list[GraphNode] = []
    label_to_id: dict[str, str] = {}

    for n in raw.nodes:
        node_id = _canonical_id(n.label)
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        label_to_id[n.label.lower().strip()] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                label=n.label,
                type=n.type,
                description=n.description,
                era=n.era,
            )
        )

    edges = []
    for e in raw.edges:
        source_id = label_to_id.get(e.source_label.lower().strip())
        target_id = label_to_id.get(e.target_label.lower().strip())
        if not source_id or not target_id or source_id == target_id:
            continue
        edges.append(
            GraphEdge(
                source=source_id,
                target=target_id,
                relationship_type=e.relationship_type,
                weight=e.weight,
            )
        )

    return SubGraphResponse(nodes=nodes, edges=edges)


@runtime_checkable
class LLMClientProtocol(Protocol):
    async def generate_graph(self, topic: str, depth: int) -> GraphResponse: ...

    async def expand_node(
        self,
        node_label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> SubGraphResponse: ...

    async def get_node_detail(
        self,
        label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> NodeDetail: ...


def _extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict:
    """Extract the tool call input dict or raise LLMRefusalError."""
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input  # type: ignore[return-value]
    raise LLMRefusalError(
        f"LLM did not call tool '{tool_name}'. "
        f"Stop reason: {response.stop_reason}. "
        f"Content types: {[b.type for b in response.content]}"
    )


class AnthropicLLMClient:
    """Production LLM client backed by the Anthropic Async SDK."""

    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_graph(self, topic: str, depth: int) -> GraphResponse:
        schema = _LLMGraphInput.model_json_schema()
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=GRAPH_SYSTEM,
            messages=[{"role": "user", "content": GRAPH_USER.format(topic=topic)}],
            tools=[
                {
                    "name": "create_knowledge_graph",
                    "description": "Create a structured knowledge graph for the given topic",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "create_knowledge_graph"},
        )
        raw_input = _extract_tool_input(response, "create_knowledge_graph")
        try:
            raw = _LLMGraphInput.model_validate(raw_input)
        except ValidationError as exc:
            raise GraphGenerationError(f"LLM returned invalid graph schema: {exc}") from exc
        return _process_llm_graph(raw, topic)

    async def expand_node(
        self,
        node_label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> SubGraphResponse:
        context_str = "\n".join(f"- {n}" for n in context_nodes) or "(none)"
        schema = _LLMSubGraphInput.model_json_schema()
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=EXPAND_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": EXPAND_USER.format(
                        node_label=node_label,
                        node_type=node_type.value,
                        context_nodes=context_str,
                    ),
                }
            ],
            tools=[
                {
                    "name": "expand_node",
                    "description": "Generate new entities connected to the selected node",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "expand_node"},
        )
        raw_input = _extract_tool_input(response, "expand_node")
        try:
            raw = _LLMSubGraphInput.model_validate(raw_input)
        except ValidationError as exc:
            raise GraphGenerationError(f"LLM returned invalid subgraph schema: {exc}") from exc
        return _process_llm_subgraph(raw)

    async def get_node_detail(
        self,
        label: str,
        node_type: NodeType,
        context_nodes: list[str],
    ) -> NodeDetail:
        context_str = ", ".join(context_nodes) or "none"
        schema = _LLMNodeDetailInput.model_json_schema()
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=NODE_DETAIL_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": NODE_DETAIL_USER.format(
                        label=label,
                        node_type=node_type.value,
                        context_nodes=context_str,
                    ),
                }
            ],
            tools=[
                {
                    "name": "get_node_detail",
                    "description": "Get detailed educational information about a knowledge graph node",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "get_node_detail"},
        )
        raw_input = _extract_tool_input(response, "get_node_detail")
        try:
            raw = _LLMNodeDetailInput.model_validate(raw_input)
        except ValidationError as exc:
            raise GraphGenerationError(f"LLM returned invalid node detail schema: {exc}") from exc
        return NodeDetail(
            label=label,
            type=node_type,
            summary=raw.summary,
            key_facts=raw.key_facts,
            date_range=raw.date_range,
            sources=raw.sources,
        )
