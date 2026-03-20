# TODOS

## Knowledge Graph Generator

### URL Input Mode (arXiv / Wikipedia → auto-graph)

**What:** Allow users to paste an arXiv paper URL or Wikipedia article URL and auto-generate a knowledge graph from the content.

**Why:** Directly serves the 'computer vision researcher reading a paper' user story without requiring manual topic typing. Fetching the paper abstract/content and passing it as context produces higher-quality, more accurate graphs than a free-text topic string alone.

**Context:** The core graph generation works via topic text input (phase 1). This feature adds a URL detection layer: if the input looks like a URL (arXiv or Wikipedia), fetch the page content server-side, extract the abstract or intro text, and pass it as context to the LLM graph generation prompt. Key implementation notes:
- Use `httpx` for fetching (already a FastAPI ecosystem dep)
- Allowlist URLs to `arxiv.org` and `en.wikipedia.org` only to prevent SSRF
- For arXiv: prefer the `/abs/` endpoint, extract `<blockquote class="abstract">`
- For Wikipedia: use the Wikipedia REST API (`/api/rest_v1/page/summary/{title}`) instead of HTML scraping
- Add URL detection as a pre-processing step in `GraphService.generate_graph()` before the LLM call
- The prompt should include the fetched content as `[CONTEXT]\n{content}\n[/CONTEXT]` prepended to the topic instruction

**Effort:** M (human: 2 days / CC+gstack: ~20 min)
**Priority:** P2
**Depends on:** Core graph generation (phase 1) working

### Era / Time-Period Filter Pills

**What:** Add a bottom pill bar that filters visible graph nodes by historical era or time period. Examples: "All Eras", "Origins (1880s–1947)", "State Formation (1947–1967)", "Peace Process (1993–2000)".

**Why:** For historical/political topics, the graph gets dense quickly. Era filtering lets users focus on a specific period to reduce cognitive load. Seen in the reference design screenshot — high visual impact.

**Context:** The LLM would assign each GraphNode an `era` field (e.g., "1947–1967" or a named period). The bottom pill bar filters D3's visible nodes to that era subset without regenerating the graph. For non-historical topics (e.g., a paper's concepts), the era field would be None and the pill bar would be hidden. Implementation notes:
- Add `era: str | None` to GraphNode schema
- LLM prompt should include era assignment instructions for historical topics
- D3 filter: hide/show nodes + their edges based on selected era pill
- The pill bar only renders if >1 distinct era is present in the graph

**Effort:** M (human: 1.5 days / CC+gstack: ~20 min)
**Priority:** P2
**Depends on:** Core graph generation working


### LLM Response Streaming (SSE)

**What:** Add a streaming endpoint (`GET /api/graph/stream?topic=X`) that uses Server-Sent Events to push nodes to the browser progressively as the LLM generates them.

**Why:** The current graph generation takes 5–15 seconds with a static loading animation. Streaming transforms "waiting" into "watching the knowledge graph build in real time" — which matches the product's core experience of discovery and exploration.

**Context:** FastAPI supports SSE via `StreamingResponse`. The Anthropic SDK supports async streaming with `async_stream()`. The tricky part is streaming partial tool call arguments — Claude generates tool arguments incrementally as text, so you'd need to buffer + parse partial JSON as it arrives, then push complete nodes as they're finalized. The frontend D3 code would need to handle incremental node additions (the expansion merge logic already handles this — reuse it). Implementation path: (1) Add `GET /api/graph/stream` endpoint using `StreamingResponse`; (2) Use `AsyncAnthropic().messages.stream(...)` with `input_json_delta` events; (3) Buffer partial JSON, emit complete node objects via SSE; (4) Frontend: replace fetch() with EventSource, call existing merge logic on each received node.

**Effort:** L (human: 1 week / CC+gstack: ~45 min)
**Priority:** P2
**Depends on:** Core graph generation (non-streaming) working

### Web Search for Node Expansion

**What:** Run a 1-query Tavily search on the node label before `expand_node()` so expanded subgraphs are also web-grounded (currently expansion uses LLM weights only).

**Why:** The main graph generation uses Tavily Research for accuracy, but when a user clicks "Expand node", the expansion still draws from LLM training weights. This creates an inconsistency: the base graph is research-grounded, but expanded nodes aren't.

**Context:** After the Tavily Research backend PR lands, `GraphService` will hold both a `research_backend` and a `search_backend`. Adding search to expansion is then straightforward:
1. In `GraphService.expand_node()`, call `self._search_backend.search([node_label])` for 1-3 results
2. Pass results as `search_context` to `self._llm.expand_node()`
3. Update `EXPAND_USER` prompt to accept and use a `[SOURCE_CONTEXT]` block
4. The `expand_node()` method on `LLMClientProtocol` would accept `search_context: list[SearchResult] | None`

**Effort:** S (human: ~2h / CC+gstack: ~10 min) — simplified by the research backend abstraction
**Priority:** P2
**Depends on:** Tavily Research backend PR (`tavily_research_for_source_generation` branch)

### Make Expand Node work

### Improve the info retrieval queries
> INFO:charlotte_knowledge_graph_generator.graph_service:Generated the following queries for topic='How neuromorphic computing works and differs from traditional computing': ['neuromorphic computing architecture brain-inspired chips explained', 'Intel Loihi IBM TrueNorth spiking neural networks hardware', 'neuromorphic vs von Neumann architecture energy efficiency comparison']

The lsat two queries are way too specific IMO

### Targeted search queries for node expansion

**What:** Generate a 1-query expansion-specific search query using a fast non-tool LLM call before the Tavily search in `expand_node()`. Instead of searching `[node_label]` verbatim, produce a targeted query like "Oslo Accords peace process 1993 signatories significance".

**Why:** Better Tavily results → richer expanded node descriptions and source citations. The current implementation (from the web-search-node-expansion PR) searches the bare node label, which works but misses context about what aspect of the entity is most relevant to expand.

**Context:** `generate_search_queries()` already exists on `LLMClientProtocol` but is currently stubbed to return `[topic]`. A simpler variant (1-query, expansion-aware) could be added as `generate_expansion_query(node_label, node_type, context_nodes)` — a fast Haiku call using the `QUERY_GEN_SYSTEM` prompt adapted for node context. Alternatively, un-stub the existing `generate_search_queries()` method once query quality is validated. Note that the `generate_search_queries` LLM call is commented out in `llm.py` — needs to be restored and tested.

**Effort:** S (human: ~1h / CC+gstack: ~5min)
**Priority:** P3
**Depends on:** Web Search for Node Expansion PR (this must ship first)

### Model Selector
Sonnet vs Haiku

### History
See what graphs are already cached

### UI changes
* move web search enabled next to explore button
* move regenerate button next to date 

### Do store tavily research in db
* Sometimes the rest of the graph creation fails, so caching helps here

## Completed

### Tavily Web Search + Per-Node Citations

Added Tavily web search before LLM graph generation so graphs are grounded in current sources rather than LLM training weights alone. Each node now carries `source_urls` (up to 4 citations shown in the panel). Added loading stage 0 ("Searching the web…"), graph timestamp, and Regenerate button to force cache bypass. Degrades gracefully when `TAVILY_API_KEY` is absent.
