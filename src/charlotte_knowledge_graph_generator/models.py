"""Pydantic models for the knowledge graph API."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    PERSON = "Person"
    EVENT = "Event"
    CONCEPT = "Concept"
    ORGANIZATION = "Organization"
    DOCUMENT = "Document"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class GraphNode(BaseModel):
    """A node in the knowledge graph."""

    id: str
    label: str
    type: NodeType
    description: str
    era: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed edge between two nodes."""

    source: str  # node id
    target: str  # node id
    relationship_type: str
    weight: int = Field(default=3, ge=1, le=5)


class GraphResponse(BaseModel):
    """Complete knowledge graph returned from the API."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    topic: str
    sources: list[SearchResult] = Field(default_factory=list)
    generated_at: datetime | None = None


class SubGraphResponse(BaseModel):
    """New nodes/edges from a node expansion (client merges into existing graph)."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class NodeDetail(BaseModel):
    """Detailed information about a single node, shown in the side panel."""

    label: str
    type: NodeType
    summary: str
    key_facts: list[str]
    date_range: str | None = None
    sources: list[str] = Field(default_factory=list)


# ── API request models ────────────────────────────────────────────────────────


class GraphRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    depth: int = Field(default=2, ge=1, le=3)
    force_refresh: bool = False


class ExpandRequest(BaseModel):
    node_id: str
    node_label: str
    node_type: NodeType
    context_nodes: list[str] = Field(default_factory=list)


class NodeDetailRequest(BaseModel):
    label: str
    node_type: NodeType
    context_nodes: list[str] = Field(default_factory=list)


# ── LLM tool input schemas (labels only — IDs are generated server-side) ─────
# These are used exclusively in llm.py to define the tool call schemas.


class _LLMNodeInput(BaseModel):
    label: str = Field(description="Human-readable entity name, e.g. 'Oslo Accords'")
    type: NodeType = Field(description="Entity type")
    description: str = Field(description="Factual 2-4 sentence description capturing the entity's significance")
    era: str | None = Field(default=None, description="Time period, e.g. '1993-2000'")


class _LLMEnrichedNodeInput(_LLMNodeInput):
    """Extended node input used only in the ENRICH stage tool schema.

    Adds source_indices so the LLM can attribute each node to search results.
    Kept separate from _LLMNodeInput to avoid polluting the SURVEY stage schema.
    """

    source_indices: list[int] = Field(
        default_factory=list,
        description="1-based indices into the provided search results list that informed this entity (1 = first result). Assign [] if no source specifically covers this entity.",
    )


class _LLMEdgeInput(BaseModel):
    source_label: str = Field(description="Label of the source node")
    target_label: str = Field(description="Label of the target node")
    relationship_type: str = Field(
        description="Short action verb phrase: 'caused', 'led to', 'opposed', 'signed', etc."
    )
    weight: int = Field(default=3, ge=1, le=5, description="Importance 1-5")


class _LLMGraphInput(BaseModel):
    nodes: list[_LLMEnrichedNodeInput] = Field(description="20-30 key entities in the final corrected graph")
    edges: list[_LLMEdgeInput] = Field(description="Directed causal connections between nodes")


class _LLMSubGraphInput(BaseModel):
    nodes: list[_LLMNodeInput] = Field(description="5-12 NEW entities connected to the selected node")
    edges: list[_LLMEdgeInput] = Field(description="Edges connecting new nodes to selected and each other")


class _LLMNodeDetailInput(BaseModel):
    summary: str = Field(description="150-200 word comprehensive explanation of the entity")
    key_facts: list[str] = Field(description="3-5 key facts as concise bullet points")
    date_range: str | None = Field(default=None, description="Active period e.g. '1920-1948', or null")
    sources: list[str] = Field(default_factory=list, description="2-4 relevant source names")


class _LLMSurveyOutput(BaseModel):
    nodes: list[_LLMNodeInput] = Field(description="25-30 key entities for the knowledge graph")


class _LLMEdgeListOutput(BaseModel):
    edges: list[_LLMEdgeInput] = Field(description="Directed causal edges connecting the entities")


class _LLMValidationIssue(BaseModel):
    severity: Literal["high", "medium"] = Field(description="Issue severity: 'high' or 'medium'")
    description: str = Field(description="Clear description of the issue and suggested fix")


class _LLMValidationOutput(BaseModel):
    issues: list[_LLMValidationIssue] = Field(
        description="List of issues found. Empty list if the graph looks good."
    )
