# Design: Readwise Source Mode + Mode Selector

Promoted from CEO plan review on 2026-03-23.
Branch: smart_node_expansion | Mode: SCOPE EXPANSION

---

## Problem

Charlotte currently generates knowledge graphs from web search results only. Users who have
annotated books in Readwise have a rich personal corpus that could produce more personally
meaningful graphs — grounded in what *they've read*, not the open web.

## Vision

Charlotte becomes a personal knowledge graph engine. Readwise is the first personal corpus
backend. The mode selector on the home screen is the entry point for a growing "source picker"
that will eventually support: Readwise, arXiv URL, Wikipedia URL, PDF upload.

The 12-month ideal: you select a source, enter a book/URL/topic, and get a graph that reflects
your specific reading and annotations — not a generic web-sourced overview.

## Scope (accepted)

### Core
- `ReadwiseSourceBackend` in `sources.py` — fetches highlights + surrounding context sentences
  from the Readwise API (v2 + v3). Adapted from sirius `readwise_api_parser` (async version).
- `readwise_api_key: str | None = None` in `Settings` (+ `readwise_context_sentences: int = 3`)
- `mode: Literal["web_search", "readwise"] = "web_search"` on `GraphRequest`
- `GraphService.generate_graph()` branches on `mode`: Readwise path skips web search entirely
- Cache key normalization: `readwise:{book_id}` — deduplicates cache entries for the same book
  regardless of how the user queried it (title vs numeric ID)
  - **Two-step lookup**: (1) call `_resolve_book_id(topic)` first (cheap, 1 API call), (2) cache
    check on `f"readwise:{book_id}"`, (3) cache miss → fetch full highlights + context + LLM
- New `LLMClientProtocol.generate_graph_from_highlights()` method (separate from `generate_graph`)
  - Reuses `_construct_edges`, `_validate_graph`, `_enrich_graph` unchanged
  - Only adds `_survey_entities_from_highlights()` as new private stage
  - Calls `_enrich_graph` with `search_results=[]` → `source_urls` will be empty for all nodes
    (no HTTP sources for book highlights; SOURCES section hidden in side panel for Readwise graphs)
- New Readwise-specific prompts: `READWISE_SURVEY_*`
  - Each highlight formatted as a numbered block:
    ```
    [1]
    <context_before>He called it System 1 and System 2.</context_before>
    <highlight>Thinking, Fast and Slow distinguishes between intuitive and deliberate cognition.</highlight>
    <context_after>System 1 operates automatically.</context_after>
    ```
  - "Highlights are your primary source for entity identification. Context is for enrichment only."
  - `_enrich_graph` (Stage 4) reused unchanged — no `READWISE_ENRICH_*` needed
- Prompt version bump to `v4` (cache bust)

### Accepted Expansions
- **Book title as graph title**: `GraphResponse.resolved_title: str | None` — the matched
  Readwise book title is returned and shown in the graph header instead of the raw query
- **Readwise provenance in info bar**: "Readwise • [Book Title]" shown in the graph info bar
- **Adaptive placeholder + example chips**: In Readwise mode, placeholder becomes
  "Enter Readwise book title or ID..." and example chips are hidden
- ~~**SourceBackend protocol**~~: **DROPPED in Eng Review** — the three backends have disparate return types and fundamentally different pipeline logic; a forced protocol adds complexity without real benefit. Document the multi-backend pattern in a `sources.py` module docstring instead.

### Frontend
- Mode selector `<select>` dropdown attached to the right of the search bar on the home screen
- `researchMode` state extended to a `sourceMode: "web_search" | "readwise"` state variable
- `generateGraph()` passes `mode` in the request body
- Loading stage messages adapt per mode (Readwise: "Fetching your highlights…")
- `readwise_available: bool` added to `/api/config` response — selector hidden if false

## NOT in scope (this PR)
- arXiv URL backend (TODOS.md — P2)
- Wikipedia URL backend (TODOS.md — P2)
- Highlight count badge (skipped)
- Highlight-seeded node visual indicator (skipped)
- Mode persisted to localStorage (skipped)
- Readwise node expansion (TODOS.md — P3)

## Critical Must-Fixes (from CEO review)

These gaps MUST be addressed before shipping:

1. **Empty highlights** → `ReadwiseNoHighlightsError` → 422 "No highlights found for this book"
2. **Invalid API key** (401) → `ReadwiseAuthError` → 422 "Invalid Readwise API key"
3. **Book not found** → `ReadwiseBookNotFoundError` → 422 "Book not found in Readwise"
4. **Network timeout** → `GraphGenerationError` → 503
5. **mode=readwise with no READWISE_API_KEY** → 422 "Readwise not configured"
6. **LLM context too long** (BadRequestError) → trim highlights + retry
7. **EPUB raw_source_url** — allowlist to `readwise.io` domains only (SSRF mitigation)
8. **Prompt injection** — wrap highlights in `<highlight>...</highlight>` XML tags

## Context Extraction (built now, fixing sirius bugs)

The `_extract_context()` function from sirius has 2 known bugs being fixed in Charlotte's version:
1. Multi-sentence highlights — find start + end sentence indices that overlap, then expand outward
2. Degenerate fragments (e.g. "e.", "a.") — include in output but don't count against N limit

## Architecture Diagram

```
sources.py                    graph_service.py                  api.py
──────────────────────────    ──────────────────────────────    ─────────────────────
# Three informal backends,    GraphService                      POST /api/graph
# each with different         __init__(                           body: {mode: ...}
# return types — no protocol    llm, cache, settings,
                                search,           ←── None or SearchService
SearchService                   research_backend, ←── None or TavilyResearchBackend
  .search() → list[SR]          readwise)         ←── None or ReadwiseSourceBackend
                                                              GET /api/config
TavilyResearchBackend         generate_graph(                   {readwise_available}
  .research() → (str, list[SR]) topic, depth,
                               mode,              ← NEW param
ReadwiseSourceBackend          force_refresh)
  .fetch(query)                  if mode=="readwise":
    → ReadwiseResult               book_id = readwise._resolve_book_id(topic)  ← FIRST
      .book_id                     cache.get(f"readwise:{book_id}")             ← THEN check
      .book_title                  MISS → readwise._fetch_full(book_id)
      .highlights[]                llm.generate_graph_from_highlights(...)
        .text                        ↳ _survey_entities_from_highlights()  ← NEW
        .context_before              ↳ _construct_edges()                  ← REUSED
        .context_after               ↳ _validate_graph()                   ← REUSED
                                     ↳ _enrich_graph(search_results=[])    ← REUSED, no URLs
                               else:  # existing path unchanged
                                 research/search + llm.generate_graph(...)
```

## Test Coverage Required

```python
# tests/test_readwise.py (new)
test_fetch_by_title_returns_highlights_with_context
test_fetch_by_book_id_returns_highlights_with_context
test_book_not_found_raises_ReadwiseBookNotFoundError
test_no_highlights_raises_ReadwiseNoHighlightsError
test_401_raises_ReadwiseAuthError
test_429_retries_with_backoff
test_epub_book_fetches_and_parses_correctly
test_context_extraction_surrounding_sentences
test_context_extraction_multisent_highlight_fix
test_context_extraction_degenerate_fragments_not_counted
test_max_highlights_cap_is_enforced
test_cache_key_uses_resolved_book_id

# tests/test_graph_service.py (additions)
test_readwise_mode_skips_web_search
test_readwise_mode_uses_resolved_title_as_topic
test_readwise_unavailable_returns_422
test_empty_highlights_returns_422
```

## Implementation Notes (from Eng Review)

### Async HTTP pattern
Single `async with httpx.AsyncClient(headers=...) as client:` opened at the top of
`ReadwiseSourceBackend.fetch()`. All private helpers (`_resolve_book_id`, `_fetch_highlights`,
`_find_reader_document`, `_fetch_document_text`) receive `client` as their first argument.
One connection pool per fetch call. Matches existing Charlotte patterns.

### ReadwiseResult + HighlightWithContext placement
Both dataclasses defined in `sources.py` (not `models.py`) — they are implementation details
of the Readwise backend, not part of the API surface.

### Test dependency
Add `respx>=0.20` to test dependencies in `pyproject.toml` for Readwise API HTTP mocking.

### lifespan startup check update
Change from "raise if no Tavily" to "raise if none of (research_backend, search_service,
readwise_backend) are configured."

### Node expansion in Readwise mode
`expand_node()` is unchanged — falls back to Tavily search if configured, LLM-only otherwise.
The `ExpandRequest` has no `mode` field. See TODOS.md "Readwise Mode for Node Expansion" (P3).

## Phase 2 Preview

After this ships, the mode selector becomes a source picker. Natural next:
- arXiv URL mode (`arxiv.org` URLs → fetch abstract → graph) — in TODOS.md P2
- Wikipedia URL mode — in TODOS.md P2
- Each new backend is a new class in `sources.py` + new branch in `generate_graph()`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 2 | ISSUES_OPEN | 7 proposals, 4 accepted, 6 critical gaps |
| Outside Voice | subagent | Independent 2nd opinion | 1 | issues_found | 8 findings, 2 substantive |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES_OPEN | 7 issues found, 5 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

**UNRESOLVED:** 0
**VERDICT:** CEO + ENG reviewed — all decisions resolved. Ready to implement.
