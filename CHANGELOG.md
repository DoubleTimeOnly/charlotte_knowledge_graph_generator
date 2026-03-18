# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0.0] - 2026-03-18

### Added
- FastAPI backend with `/api/graph`, `/api/expand`, `/api/node/detail` endpoints for knowledge graph generation and exploration
- `AnthropicLLMClient` wrapping the Anthropic Async SDK with typed tool-use output; uses `create_knowledge_graph`, `expand_node`, and `get_node_detail` tools
- `GraphService` with exponential-backoff retry (`_with_retry`) for `APITimeoutError` and `RateLimitError`; enforces server-side node caps for graphs and expansions
- SQLite `CacheLayer` via `aiosqlite` with WAL mode; caches graphs and node details keyed by SHA-256 hash of (topic, depth, prompt_version)
- Pydantic models for all request/response shapes; LLM tool output validated through separate internal schemas before being converted to API models
- Rate limiting via `slowapi` (configurable per-minute limit per IP)
- `config.py` with `pydantic-settings` for environment-based configuration (API key, model, cache path, node caps, rate limit, prompt version)
- D3.js force-directed graph frontend (`static/`) with node type colouring, click-to-expand, side panel node details, search/filter, empty states, and loading skeletons
- UX fixes: topic input auto-focus, search clearing, empty-state copy, description text clamping
- 94 pytest tests covering models, cache (including error/disabled paths), graph service (including retry logic), and API routes (including Anthropic exception handlers)
- `.gitignore` entries for `.env`, `cache.db`, WAL files, and `.gstack/`
- `TODOS.md` with P1–P2 backlog items: URL input mode (arXiv/Wikipedia), era filter pills, DESIGN.md, and LLM streaming
