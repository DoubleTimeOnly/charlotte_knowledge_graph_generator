# Charlotte — Knowledge Graph Generator

Explore any topic as an interactive knowledge graph. Enter a subject and Charlotte generates a graph of key people, events, concepts, organizations, and documents — click any node to learn more or expand it deeper.

When a [Tavily API key](#configuration) is configured, Charlotte searches the web before generating each graph, producing more accurate and up-to-date results with per-node source citations.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- An [Anthropic API key](https://console.anthropic.com/)
- *(Optional)* A [Tavily API key](https://tavily.com/) for web-grounded graphs

## Quickstart

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd charlotte_knowledge_graph_generator
uv sync

# 2. Set your API key (add TAVILY_API_KEY for web search)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Start the server
uv run uvicorn charlotte_knowledge_graph_generator.api:app --reload

# 4. Open http://localhost:8000
```

## Configuration

All settings are read from environment variables or a `.env` file in the project root.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `TAVILY_API_KEY` | *(optional)* | Tavily search API key — enables web-grounded generation and per-node citations. If absent, graphs are generated from LLM weights only. |
| `CACHE_DB_PATH` | `cache.db` | SQLite cache file path |
| `MAX_NODES_PER_GRAPH` | `25` | Node cap for initial graph generation |
| `MAX_NODES_PER_EXPAND` | `12` | Node cap per expansion |
| `RATE_LIMIT_PER_MINUTE` | `10` | API requests per IP per minute |
| `STATIC_DIR` | `static` | Directory serving the frontend |
| `PROMPT_VERSION` | `v2` | Cache-busting key — bump when prompts change |
| `SEARCH_MAX_RESULTS_PER_QUERY` | `5` | Tavily results fetched per search query |
| `SEARCH_NUM_QUERIES` | `3` | Number of search queries generated per topic |

## Web Search

When `TAVILY_API_KEY` is set:

1. Charlotte generates 2–3 targeted search queries for the topic using a fast Claude call
2. Queries run in parallel via Tavily and results are deduplicated
3. Search results are injected as context into the graph generation pipeline
4. Each node is tagged with the source URLs that informed it — shown as clickable citation links in the side panel

If search fails for any reason (network error, rate limit, missing key), Charlotte falls back silently to LLM-only generation. No error is shown to the user.

The graph toolbar shows when a graph was generated and a **↺ Regenerate** button to force a fresh search and bypass the cache.

## Development

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
