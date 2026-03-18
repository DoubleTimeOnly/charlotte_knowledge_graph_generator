"""Tests for Pydantic models and helper functions."""

import pytest
from pydantic import ValidationError

from charlotte_knowledge_graph_generator.llm import _canonical_id
from charlotte_knowledge_graph_generator.models import (
    GraphEdge,
    GraphNode,
    GraphRequest,
    NodeType,
)


class TestCanonicalId:
    def test_spaces_become_underscores(self):
        assert _canonical_id("Oslo Accords") == "oslo_accords"

    def test_hyphens_become_underscores(self):
        assert _canonical_id("Two-State Solution") == "two_state_solution"

    def test_lowercased(self):
        assert _canonical_id("PLO") == "plo"

    def test_strips_whitespace(self):
        assert _canonical_id("  Yasser Arafat  ") == "yasser_arafat"

    def test_mixed_hyphens_and_spaces(self):
        assert _canonical_id("Balfour Declaration-1917") == "balfour_declaration_1917"

    def test_already_lowercase_no_spaces(self):
        assert _canonical_id("hamas") == "hamas"


class TestNodeType:
    def test_all_values_present(self):
        values = {t.value for t in NodeType}
        assert values == {"Person", "Event", "Concept", "Organization", "Document"}

    def test_is_string_enum(self):
        assert isinstance(NodeType.PERSON, str)
        assert NodeType.PERSON == "Person"


class TestGraphNode:
    def test_valid_construction(self):
        node = GraphNode(
            id="oslo_accords",
            label="Oslo Accords",
            type=NodeType.DOCUMENT,
            description="1993 peace agreements.",
        )
        assert node.id == "oslo_accords"
        assert node.type == NodeType.DOCUMENT
        assert node.era is None

    def test_era_optional(self):
        node = GraphNode(
            id="n1",
            label="Some Event",
            type=NodeType.EVENT,
            description="desc",
            era="1948",
        )
        assert node.era == "1948"

    def test_type_accepts_string_value(self):
        node = GraphNode(
            id="n1", label="X", type="Person", description="desc"  # type: ignore[arg-type]
        )
        assert node.type == NodeType.PERSON

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            GraphNode(id="n1", label="X", type="Alien", description="desc")  # type: ignore[arg-type]


class TestGraphEdge:
    def test_default_weight(self):
        edge = GraphEdge(source="a", target="b", relationship_type="caused")
        assert edge.weight == 3

    def test_weight_at_boundaries(self):
        GraphEdge(source="a", target="b", relationship_type="r", weight=1)
        GraphEdge(source="a", target="b", relationship_type="r", weight=5)

    def test_weight_below_min_raises(self):
        with pytest.raises(ValidationError):
            GraphEdge(source="a", target="b", relationship_type="r", weight=0)

    def test_weight_above_max_raises(self):
        with pytest.raises(ValidationError):
            GraphEdge(source="a", target="b", relationship_type="r", weight=6)


class TestGraphRequest:
    def test_empty_topic_raises(self):
        with pytest.raises(ValidationError):
            GraphRequest(topic="")

    def test_topic_too_long_raises(self):
        with pytest.raises(ValidationError):
            GraphRequest(topic="x" * 501)

    def test_default_depth(self):
        from charlotte_knowledge_graph_generator.models import GraphRequest

        req = GraphRequest(topic="valid topic")
        assert req.depth == 2

    def test_depth_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            GraphRequest(topic="t", depth=0)
        with pytest.raises(ValidationError):
            GraphRequest(topic="t", depth=4)
