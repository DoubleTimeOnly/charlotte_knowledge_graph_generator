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

### Create DESIGN.md — Canonical Design System Reference

**What:** Formalize all design tokens and specs from the /plan-design-review session into a DESIGN.md file.

**Why:** Without it, any future contributor who touches the frontend re-derives these decisions ad hoc, producing inconsistency. The tokens (colors, typography, spacing, node sizing, interaction states, mobile layout) are already fully specified — they just need to be written down.

**Context:** The /plan-design-review session (2026-03-18) produced the full design system:
- Color tokens for each node type (Person, Event, Concept, Organization, Document)
- Graph canvas background: warm off-white #F7F5F0
- Typography: Inter for UI chrome, DM Mono for labels
- Node sizing: degree-proportional (hub=22px, mid=16px, leaf=11px)
- All interaction states (loading, empty, error, success) per feature
- Mobile layout: full-screen graph + bottom-sheet panel on tap
- Keyboard navigation spec
- ARIA roles and accessibility spec
The design session also incorporated a reference screenshot showing the desired visual direction (persistent side panel, always-visible labels, circle nodes differentiated by color only, era filter pills at bottom).

**Effort:** S (human: 1 day / CC+gstack: ~10 min)
**Priority:** P1
**Depends on:** None — can be written immediately

### LLM Response Streaming (SSE)

**What:** Add a streaming endpoint (`GET /api/graph/stream?topic=X`) that uses Server-Sent Events to push nodes to the browser progressively as the LLM generates them.

**Why:** The current graph generation takes 5–15 seconds with a static loading animation. Streaming transforms "waiting" into "watching the knowledge graph build in real time" — which matches the product's core experience of discovery and exploration.

**Context:** FastAPI supports SSE via `StreamingResponse`. The Anthropic SDK supports async streaming with `async_stream()`. The tricky part is streaming partial tool call arguments — Claude generates tool arguments incrementally as text, so you'd need to buffer + parse partial JSON as it arrives, then push complete nodes as they're finalized. The frontend D3 code would need to handle incremental node additions (the expansion merge logic already handles this — reuse it). Implementation path: (1) Add `GET /api/graph/stream` endpoint using `StreamingResponse`; (2) Use `AsyncAnthropic().messages.stream(...)` with `input_json_delta` events; (3) Buffer partial JSON, emit complete node objects via SSE; (4) Frontend: replace fetch() with EventSource, call existing merge logic on each received node.

**Effort:** L (human: 1 week / CC+gstack: ~45 min)
**Priority:** P2
**Depends on:** Core graph generation (non-streaming) working

## Completed
