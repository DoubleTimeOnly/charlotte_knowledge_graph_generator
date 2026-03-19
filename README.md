# Charlotte — Knowledge Graph Generator

Explore any topic as an interactive knowledge graph. Enter a subject and Charlotte generates a graph of key people, events, concepts, organizations, and documents — click any node to learn more or expand it deeper.

Charlotte supports two generation modes depending on which Tavily API key is configured:

- **Web Search** — Charlotte runs targeted Tavily searches before generating, adding source citations to each node
- **Deep Research** — Charlotte uses Tavily's autonomous Research API to produce a synthesized overview before graph generation, yielding the most accurate and comprehensive results

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- An [Anthropic API key](https://console.anthropic.com/)
- A [Tavily API key](https://tavily.com/) for web search mode, **or** a [Tavily Research API key](https://tavily.com/) for deep research mode (at least one required)

## Quickstart

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd charlotte_knowledge_graph_generator
uv sync

# 2. Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
# Optionally add TAVILY_RESEARCH_API_KEY for deep research, or TAVILY_API_KEY for web search

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
| `CACHE_DB_PATH` | `cache.db` | SQLite cache file path |
| `MAX_NODES_PER_GRAPH` | `25` | Node cap for initial graph generation |
| `MAX_NODES_PER_EXPAND` | `12` | Node cap per expansion |
| `RATE_LIMIT_PER_MINUTE` | `10` | API requests per IP per minute |
| `STATIC_DIR` | `static` | Directory serving the frontend |
| `PROMPT_VERSION` | `v3` | Cache-busting key — bump when prompts change |
| `RESEARCH_TIMEOUT_SECS` | `120` | Timeout for Tavily Research API calls |
| `SEARCH_MAX_RESULTS_PER_QUERY` | `5` | Tavily results fetched per search query (web search mode) |
| `SEARCH_NUM_QUERIES` | `3` | Number of search queries generated per topic (web search mode) |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Generation Modes

### Deep Research (recommended)

Set `TAVILY_RESEARCH_API_KEY`. When a graph is requested, Charlotte calls Tavily's autonomous Research API, which searches and synthesizes multiple sources into a comprehensive overview (takes 10–60 seconds). This overview is injected into the graph generation pipeline alongside the source URLs, producing the most accurate and up-to-date graphs.

The loading indicator shows "Researching topic in depth…" during the research phase.

### Web Search

Set `TAVILY_API_KEY` (without `TAVILY_RESEARCH_API_KEY`). Charlotte generates 2–3 targeted search queries and runs them in parallel via Tavily. Search results are injected as context into the graph generation pipeline, and each node is tagged with the source URLs that informed it — shown as clickable citation links in the side panel.

### LLM-only

No Tavily key required. Graphs are generated entirely from Claude's training knowledge. Quality is good for well-documented topics but may lag on recent events.

### Fallback behaviour

If Deep Research or Web Search fails for any reason (network error, rate limit, timeout), Charlotte falls back silently to LLM-only generation. No error is shown to the user.

The graph toolbar shows when a graph was generated and a **↺ Regenerate** button to force a fresh generation and bypass the cache.

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
| `POST` | `/api/graph` | Generate a knowledge graph for a topic |
| `POST` | `/api/expand` | Expand a node with connected entities |
| `POST` | `/api/node/detail` | Get detailed info for a node |
| `GET` | `/admin/cache/stats` | Cache hit/miss stats |

### `POST /api/graph`
```json
{ "topic": "Israel-Palestine conflict", "depth": 2, "force_refresh": false }
```

`force_refresh: true` bypasses the cache read and regenerates from a fresh web search.

### `POST /api/expand`
```json
{ "node_id": "oslo_accords", "node_label": "Oslo Accords", "node_type": "Document", "context_nodes": ["Yasser Arafat", "PLO"] }
```

### `POST /api/node/detail`
```json
{ "label": "Oslo Accords", "node_type": "Document", "context_nodes": ["Yasser Arafat"] }
```
