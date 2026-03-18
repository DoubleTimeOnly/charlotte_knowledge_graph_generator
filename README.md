# Charlotte — Knowledge Graph Generator

Explore any topic as an interactive knowledge graph. Enter a subject and Charlotte generates a graph of key people, events, concepts, organizations, and documents — click any node to learn more or expand it deeper.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- An [Anthropic API key](https://console.anthropic.com/)

## Quickstart

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd charlotte_knowledge_graph_generator
uv sync

# 2. Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Start the server
uv run uvicorn charlotte_knowledge_graph_generator.core:app --reload

# 4. Open http://localhost:8000
```

## Configuration

All settings are read from environment variables or a `.env` file in the project root.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `CACHE_DB_PATH` | `cache.db` | SQLite cache file path |
| `MAX_NODES_PER_GRAPH` | `25` | Node cap for initial graph generation |
| `MAX_NODES_PER_EXPAND` | `12` | Node cap per expansion |
| `RATE_LIMIT_PER_MINUTE` | `10` | API requests per IP per minute |
| `STATIC_DIR` | `static` | Directory serving the frontend |
| `PROMPT_VERSION` | `v1` | Cache-busting key — bump when prompts change |

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
{ "topic": "Israel-Palestine conflict", "depth": 2 }
```

### `POST /api/expand`
```json
{ "node_id": "oslo_accords", "node_label": "Oslo Accords", "node_type": "Document", "context_nodes": ["Yasser Arafat", "PLO"] }
```

### `POST /api/node/detail`
```json
{ "label": "Oslo Accords", "node_type": "Document", "context_nodes": ["Yasser Arafat"] }
```
