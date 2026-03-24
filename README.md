# Charlotte — Knowledge Graph Generator

Explore any topic as an interactive knowledge graph. Enter a subject and Charlotte generates a graph of key people, events, concepts, organizations, and documents — click any node to learn more or expand it deeper.

Charlotte supports three generation modes:

- **Web Search** — Charlotte runs targeted Tavily searches before generating, adding source citations to each node
- **Deep Research** — Charlotte uses Tavily's autonomous Research API to produce a synthesized overview before graph generation, yielding the most accurate and comprehensive results
- **Readwise** — Charlotte generates the graph from your personal Readwise highlights, using surrounding sentence context to enrich entity relationships without hitting the web

Both initial graph generation and **node expansion** run the same 5-stage pipeline: QUERY_GEN → SURVEY → EDGES → VALIDATE → ENRICH. In Readwise mode, SURVEY is replaced by a highlight-aware stage and web search is skipped entirely.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- An [Anthropic API key](https://console.anthropic.com/)
- At least one of:
  - A [Tavily API key](https://tavily.com/) for web search mode
  - A [Tavily Research API key](https://tavily.com/) for deep research mode
  - A [Readwise API key](https://readwise.io/access_token) for Readwise mode

## Quickstart

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd charlotte_knowledge_graph_generator
uv sync

# 2. Set your API keys
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
# Add at least one source:
echo "TAVILY_API_KEY=tvly-..."       >> .env   # web search
echo "TAVILY_RESEARCH_API_KEY=..."   >> .env   # deep research (optional, takes priority)
echo "READWISE_API_KEY=..."          >> .env   # Readwise mode

# 3. Start the server
uv run uvicorn charlotte_knowledge_graph_generator.api:app --reload
# or to make this accessible to other devices on the same network
uv run uvicorn charlotte_knowledge_graph_generator.api:app --reload --host 0.0.0.0

# 4. Open http://localhost:8000
```

## Configuration

All settings are read from environment variables or a `.env` file in the project root.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `TAVILY_RESEARCH_API_KEY` | *(optional)* | Tavily Research API key — enables deep research mode. Takes priority over `TAVILY_API_KEY` for graph generation. |
| `TAVILY_API_KEY` | *(optional)* | Tavily search API key — enables web search mode and per-node citations. Used for graph generation when no Research key is set. |
| `READWISE_API_KEY` | *(optional)* | Readwise API key — enables Readwise mode. Get yours at readwise.io/access_token. |
| `READWISE_CONTEXT_SENTENCES` | `3` | Sentences of surrounding text fetched before/after each highlight for context |
| `READWISE_MAX_HIGHLIGHTS` | `100` | Maximum highlights fetched per book (capped to avoid token limits) |
| `CACHE_DB_PATH` | `cache.db` | SQLite cache file path |
| `MAX_NODES_PER_GRAPH` | `25` | Node cap for initial graph generation |
| `MAX_NODES_PER_EXPAND` | `10` | Maximum new nodes added per expansion |
| `RATE_LIMIT_PER_MINUTE` | `10` | API requests per IP per minute |
| `STATIC_DIR` | `static` | Directory serving the frontend |
| `PROMPT_VERSION` | `v4` | Cache-busting key — bump when prompts change |
| `RESEARCH_TIMEOUT_SECS` | `120` | Timeout for Tavily Research API calls |
| `SEARCH_MAX_RESULTS_PER_QUERY` | `5` | Tavily results fetched per search query (web search mode) |
| `SEARCH_NUM_QUERIES` | `3` | Number of search queries generated per topic (web search mode) |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Generation Modes

### Web Search

Set `TAVILY_API_KEY` (without `TAVILY_RESEARCH_API_KEY`). Charlotte generates 2–3 targeted search queries and runs them in parallel via Tavily. Search results are injected as context into the graph generation pipeline, and each node is tagged with the source URLs that informed it — shown as clickable citation links in the side panel.

### Deep Research

Set `TAVILY_RESEARCH_API_KEY`. When a graph is requested, Charlotte calls Tavily's autonomous Research API, which searches and synthesizes multiple sources into a comprehensive overview (takes 10–60 seconds). This overview is injected into the graph generation pipeline alongside the source URLs, producing the most accurate and up-to-date graphs.

The loading indicator shows "Researching topic in depth…" during the research phase.

### Readwise

Set `READWISE_API_KEY`. A **Readwise** option appears in the source mode dropdown next to the search bar.

Enter a book title (e.g. *Thinking, Fast and Slow*) or a numeric book ID. Charlotte:

1. Resolves the book ID via the Readwise API (used as the cache key so title and ID queries share the same cached result)
2. Fetches your highlights for that book, along with up to `READWISE_CONTEXT_SENTENCES` surrounding sentences from the full article text via Readwise Reader
3. Generates the knowledge graph from the highlights only — entities are seeded exclusively from the passages you marked as important
4. Uses surrounding context to enrich edge relationships without adding context-only nodes to the graph

The info bar shows "Readwise • [Book Title]" to distinguish Readwise-generated graphs from web-search ones. Web search is never called in this mode.

### Mode selector

The dropdown is hidden until the server confirms which backends are available (via `GET /api/config`). If only one backend is configured, no dropdown is shown — the available mode is used automatically.

## Development

For verbose logs during development (shows LLM stage timings, cache hits/misses, search query details):

```bash
LOG_LEVEL=DEBUG uv run uvicorn charlotte_knowledge_graph_generator.api:app --reload
```

Or add `LOG_LEVEL=DEBUG` to your `.env` file.

Run tests:

```bash
uv run pytest
```

Run linting and formatting:

```bash
uv run ruff check .
uv run black .
uv run mypy src/
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the frontend |
| `GET` | `/health` | Health check |
| `GET` | `/api/config` | Returns available source backends (`readwise_available`, etc.) |
| `POST` | `/api/graph` | Generate a knowledge graph for a topic |
| `POST` | `/api/expand` | Expand a node with connected entities |
| `POST` | `/api/node/detail` | Get detailed info for a node |
| `GET` | `/admin/cache/stats` | Cache hit/miss stats |

### `POST /api/graph`
```json
{ "topic": "Israel-Palestine conflict", "depth": 2, "force_refresh": false, "mode": "web_search" }
```

`mode` is `"web_search"` (default) or `"readwise"`. `force_refresh: true` bypasses the cache and regenerates fresh.

For Readwise mode, `topic` is the book title or numeric book ID:
```json
{ "topic": "Thinking, Fast and Slow", "depth": 2, "mode": "readwise" }
```

### `POST /api/expand`
```json
{
  "node_id": "oslo_accords",
  "node_label": "Oslo Accords",
  "node_type": "Document",
  "context_nodes": ["Yasser Arafat", "PLO", "...all current node labels"],
  "seed_nodes": [
    { "id": "oslo_accords", "label": "Oslo Accords", "type": "Document", "description": "..." },
    { "id": "yasser_arafat", "label": "Yasser Arafat", "type": "Person", "description": "..." }
  ]
}
```

`seed_nodes` should contain the selected node plus its direct neighbors in the current graph. The expansion pipeline uses them as the starting point for discovery — these nodes are never re-generated, only connected to.

### `POST /api/node/detail`
```json
{ "label": "Oslo Accords", "node_type": "Document", "context_nodes": ["Yasser Arafat"] }
```
