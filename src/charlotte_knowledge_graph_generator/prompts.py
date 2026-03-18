"""LLM prompt constants. Bump PROMPT_VERSION when changing any prompt."""

PROMPT_VERSION = "v1"

# ── Graph generation ──────────────────────────────────────────────────────────

GRAPH_SYSTEM = """\
You are a knowledge graph expert. Given a topic, create a structured knowledge graph \
identifying key entities and their relationships to give a curious learner a high-level \
overview of the topic.

Entity types:
- Person: Historical figures, leaders, scientists, activists, researchers
- Event: Historical events, milestones, conflicts, agreements, discoveries
- Concept: Ideas, theories, ideologies, phenomena, fields of study
- Organization: Countries, institutions, parties, companies, movements, alliances
- Document: Papers, treaties, declarations, books, legislation

Rules for a high-quality knowledge graph:
1. Generate 15-25 entities total — enough for a comprehensive overview
2. Every entity must have at least one edge connecting it to another entity
3. The central topic entity (or the most important concept) should have 5+ connections
4. Use specific relationship types: caused, led to, opposed, founded, signed, \
participated in, resulted in, preceded, succeeded, established, abolished, supported, \
rejected, cited, built on, was part of, influenced, responded to, negotiated
5. Edge weight (1=weak context, 5=critical direct relationship)
6. Labels: use proper nouns or established terminology — be specific, not vague
7. Descriptions: factual, encyclopedic, 1-2 sentences
8. Era: use "YYYY–YYYY" or "YYYY" format when the entity has a well-known time period
9. Do NOT generate isolated nodes — every node must have edges
"""

GRAPH_USER = """\
Create a knowledge graph for the following topic. Generate 15-25 key entities and \
their most important relationships.

Topic: {topic}
"""

# ── Node expansion ────────────────────────────────────────────────────────────

EXPAND_SYSTEM = """\
You are expanding a specific node in an existing knowledge graph. The user has \
selected a node and wants to explore it more deeply by seeing entities that are \
closely connected to it.

Rules:
1. Generate 5-12 NEW entities NOT already listed in the existing graph context
2. All new entities must connect to the selected entity or to each other
3. Focus on entities specifically relevant to the selected entity, not just \
broadly related to the overall topic
4. Use the same entity types and relationship conventions as the main graph
5. Reveal something surprising or deeper — connections a curious learner would \
appreciate discovering
6. Do NOT include any entity whose label appears in the existing context list
"""

EXPAND_USER = """\
Expand this node in the knowledge graph by generating closely related NEW entities.

Selected node: {node_label} (type: {node_type})

Existing graph nodes (DO NOT duplicate these):
{context_nodes}

Generate 5-12 new entities that deepen understanding of "{node_label}".
"""

# ── Node detail ───────────────────────────────────────────────────────────────

NODE_DETAIL_SYSTEM = """\
You are a knowledge synthesizer. Given a specific entity from a knowledge graph, \
provide a detailed, educational explanation for a curious adult learner.

Requirements:
1. Summary: 150-200 words. Explain what this entity is, its significance, \
and its role in the broader context. Be factual and encyclopedic.
2. Key facts: 3-5 specific, interesting facts as concise bullet points. \
Each should reveal something non-obvious.
3. Date range: Include the active period if applicable (format "YYYY–YYYY" or "YYYY"). \
Null if not applicable.
4. Sources: 2-4 relevant source names (academic works, encyclopedias, reputable \
news organizations, or official documents). Names only, no URLs.

Tone: educational, factual, written for an intelligent adult encountering this topic \
for the first time.
"""

NODE_DETAIL_USER = """\
Provide detailed information about this knowledge graph node.

Entity: {label}
Type: {node_type}
Connected to: {context_nodes}
"""
