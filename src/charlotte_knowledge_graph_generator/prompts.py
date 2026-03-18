"""LLM prompt constants. Bump PROMPT_VERSION when changing any prompt."""

PROMPT_VERSION = "v2"

# ── Graph generation — 5-stage pipeline ───────────────────────────────────────
#
# Stage 0 QUERY_GEN: generate 2-3 targeted search queries for the topic
# Stage 0.5 SEARCH:  run queries via Tavily (async, outside LLM pipeline)
# Stage 1 SURVEY:    identify ~25-30 key entities using a causal bottleneck test
# Stage 2 EDGES:     build directed causal edges using a necessity test
# Stage 3 VALIDATE:  review the graph for structural issues
# Stage 4 ENRICH:    produce the final corrected graph with source attribution

QUERY_GEN_SYSTEM = """\
Generate exactly 2-3 search queries to research the topic provided.
Each query on its own line. No bullets, no numbers, no punctuation at the end.
Queries should cover different angles: overview, key figures/events, historical context.
For technical topics (papers, algorithms), focus on the specific work and its context.
"""

SURVEY_SYSTEM = """\
You are a knowledge graph expert. Given a topic, identify the key entities that \
belong in a causal knowledge graph.

You have access to recent web search results about this topic. Use them to identify \
accurate, current entities. Treat them as authoritative sources for facts and context.

Entity types:
- Person: Historical figures, leaders, scientists, activists, researchers
- Event: Historical events, milestones, conflicts, agreements, discoveries
- Concept: Ideas, theories, ideologies, phenomena, fields of study
- Organization: Countries, institutions, parties, companies, movements, alliances
- Document: Papers, treaties, declarations, books, legislation

Rules:
1. Generate 25-30 entities total across all types
2. Apply the CAUSAL BOTTLENECK TEST: only include an entity if removing it would \
make downstream events unexplainable. Exclude entities that are merely associated.
3. Aim for roughly: 30% Events, 25% People, 20% Organizations, 15% Concepts, 10% Documents
4. Labels: use proper nouns or established terminology — be specific, not vague
5. Descriptions: factual, encyclopedic, 2-4 sentences capturing the entity's significance
6. Era: use "YYYY–YYYY" or "YYYY" format when the entity has a well-known time period
"""

SURVEY_USER = """\
Identify the key causal entities for a knowledge graph about this topic.

Topic: {topic}

[SOURCE_CONTEXT]
{search_context}
[/SOURCE_CONTEXT]
"""

EDGES_SYSTEM = """\
You are a knowledge graph expert. Given a list of entities, construct the directed \
causal edges between them.

Rules:
1. Generate 40-60 directed edges
2. CAUSAL DIRECTION: A→B means A caused, enabled, or triggered B. Direction matters.
3. NECESSITY TEST: only add an edge if explaining B requires mentioning A. \
Skip edges where the connection is merely associative or contextual.
4. NO TRANSITIVE SHORTCUTS: do not add A→C if the only path is A→B→C. \
Add A→B and B→C instead.
5. Edge labels must be specific action verbs: triggered, signed, founded, led_to, \
opposed, established, abolished, enabled, resulted_in, preceded, negotiated, \
cited, built_on, responded_to. Avoid vague labels like "related_to" or "connected".
6. Edge weight (1=weak causal link, 5=direct and critical cause)
7. Every entity should have at least one incoming or outgoing edge
"""

EDGES_USER = """\
Construct directed causal edges for this knowledge graph.

Topic: {topic}

Entities:
{json_nodes}
"""

VALIDATE_SYSTEM = """\
You are a knowledge graph quality reviewer. Review the given graph and identify \
structural issues.

Check for:
1. ORPHAN NODES: entities with zero connections
2. MISSING CAUSAL LINKS: obvious causal relationships that are absent
3. REDUNDANT NODES: entities that duplicate another node's role
4. VAGUE EDGE LABELS: relationship types that don't describe a specific action
5. MISSING ERA: important entities with no time period set

For each issue, specify severity:
- high: structural problem that breaks graph integrity (orphans, missing critical links)
- medium: quality problem that reduces graph value (vague labels, missing eras)

Return an empty list if the graph has no significant issues.
"""

VALIDATE_USER = """\
Review this knowledge graph for structural and quality issues.

{json_graph}
"""

ENRICH_SYSTEM = """\
You are a knowledge graph expert. Given a knowledge graph and a list of validation \
issues, produce the final corrected graph.

Rules:
1. Apply ALL high-severity fixes
2. Apply medium-severity fixes where they don't add unnecessary complexity
3. The final graph should have 20-30 nodes and 30-50 edges
4. Maintain the same entity types, description style, and edge conventions as the input
5. Do NOT add new entities unless required to fix a high-severity issue
6. Every entity must have at least one edge

Source attribution: For each node, add source_indices — the 1-based indices from the \
SOURCE_CONTEXT below whose content mentions or supports this entity. \
For example, use 1 for [1], 2 for [2], etc. \
Assign [] if no source specifically covers this entity.
"""

ENRICH_USER = """\
Produce the final corrected knowledge graph.

Original graph:
{json_graph}

Validation issues:
{validation_issues}

[SOURCE_CONTEXT]
{search_context}
[/SOURCE_CONTEXT]
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
