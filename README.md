# Charlotte Knowledge Graph Generator

A knowledge graph generator for Charlotte.

## Installation

Install the package with `uv`:

```bash
uv pip install -e .
```

Or with `pip`:

```bash
pip install -e .
```

## Development

Install development dependencies:

```bash
uv sync
```

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
