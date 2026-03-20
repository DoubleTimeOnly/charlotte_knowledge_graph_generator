"""SQLite cache layer using aiosqlite.

Cache design:
  graphs     table:  key TEXT PRIMARY KEY, data TEXT (JSON), created_at REAL
  nodes      table:  key TEXT PRIMARY KEY, data TEXT (JSON), created_at REAL
  expansions table:  key TEXT PRIMARY KEY, data TEXT (JSON), created_at REAL

Cache keys:
  graph:     sha256(topic_lower + ":" + depth + ":" + prompt_version)
  node:      sha256(label_lower + ":" + node_type + ":" + prompt_version)
  expansion: sha256("expansion:" + label_lower + ":" + node_type + ":" + prompt_version + ":" + seed_labels)

On OperationalError (disk full, locked, etc.): log and degrade gracefully —
callers receive None and fall through to the LLM.
"""

import hashlib
import json
import logging
import sqlite3
import time

import aiosqlite

from charlotte_knowledge_graph_generator.models import GraphResponse, NodeDetail, SubGraphResponse

logger = logging.getLogger(__name__)

_CREATE_GRAPHS = """
CREATE TABLE IF NOT EXISTS graphs (
    key      TEXT PRIMARY KEY,
    data     TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    key        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""

_CREATE_EXPANSIONS = """
CREATE TABLE IF NOT EXISTS expansions (
    key        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def _graph_key(topic: str, depth: int, prompt_version: str) -> str:
    raw = f"{topic.lower().strip()}:{depth}:{prompt_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _node_key(label: str, node_type: str, prompt_version: str) -> str:
    raw = f"{label.lower().strip()}:{node_type}:{prompt_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _expansion_key(label: str, node_type: str, prompt_version: str, seed_labels: str = "") -> str:
    raw = f"expansion:{label.lower().strip()}:{node_type}:{prompt_version}:{seed_labels}"
    return hashlib.sha256(raw.encode()).hexdigest()


class CacheLayer:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def setup(self) -> None:
        """Open connection and create tables. Call once during app lifespan."""
        try:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute(_CREATE_GRAPHS)
            await self._db.execute(_CREATE_NODES)
            await self._db.execute(_CREATE_EXPANSIONS)
            await self._db.commit()
            logger.info("Cache initialised at %s", self._db_path)
        except (aiosqlite.Error, sqlite3.Error) as exc:
            logger.warning("Cache setup failed (%s) — running without cache", exc)
            self._db = None

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Graph cache ───────────────────────────────────────────────────────────

    async def get_graph(self, topic: str, depth: int, prompt_version: str) -> GraphResponse | None:
        if self._db is None:
            return None
        key = _graph_key(topic, depth, prompt_version)
        try:
            async with self._db.execute(
                "SELECT data FROM graphs WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return GraphResponse.model_validate_json(row[0])
        except (aiosqlite.Error, sqlite3.Error, ValueError) as exc:
            logger.warning("Cache read error (graph): %s", exc)
            return None

    async def set_graph(
        self, topic: str, depth: int, prompt_version: str, graph: GraphResponse
    ) -> None:
        if self._db is None:
            return
        key = _graph_key(topic, depth, prompt_version)
        try:
            await self._db.execute(
                "INSERT OR REPLACE INTO graphs (key, data, created_at) VALUES (?, ?, ?)",
                (key, graph.model_dump_json(), time.time()),
            )
            await self._db.commit()
        except (aiosqlite.Error, sqlite3.Error) as exc:
            logger.warning("Cache write error (graph): %s — continuing without cache", exc)

    # ── Node detail cache ─────────────────────────────────────────────────────

    async def get_node_detail(
        self, label: str, node_type: str, prompt_version: str
    ) -> NodeDetail | None:
        if self._db is None:
            return None
        key = _node_key(label, node_type, prompt_version)
        try:
            async with self._db.execute(
                "SELECT data FROM nodes WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return NodeDetail.model_validate_json(row[0])
        except (aiosqlite.Error, sqlite3.Error, ValueError) as exc:
            logger.warning("Cache read error (node): %s", exc)
            return None

    async def set_node_detail(
        self, label: str, node_type: str, prompt_version: str, detail: NodeDetail
    ) -> None:
        if self._db is None:
            return
        key = _node_key(label, node_type, prompt_version)
        try:
            await self._db.execute(
                "INSERT OR REPLACE INTO nodes (key, data, created_at) VALUES (?, ?, ?)",
                (key, detail.model_dump_json(), time.time()),
            )
            await self._db.commit()
        except (aiosqlite.Error, sqlite3.Error) as exc:
            logger.warning("Cache write error (node): %s — continuing without cache", exc)

    # ── Expansion cache ───────────────────────────────────────────────────────

    async def get_expansion(
        self, label: str, node_type: str, prompt_version: str, seed_labels: str = ""
    ) -> SubGraphResponse | None:
        if self._db is None:
            return None
        key = _expansion_key(label, node_type, prompt_version, seed_labels)
        try:
            async with self._db.execute(
                "SELECT data FROM expansions WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return SubGraphResponse.model_validate_json(row[0])
        except (aiosqlite.Error, sqlite3.Error, ValueError) as exc:
            logger.warning("Cache read error (expansion): %s", exc)
            return None

    async def set_expansion(
        self, label: str, node_type: str, prompt_version: str, expansion: SubGraphResponse, seed_labels: str = ""
    ) -> None:
        if self._db is None:
            return
        key = _expansion_key(label, node_type, prompt_version, seed_labels)
        try:
            await self._db.execute(
                "INSERT OR REPLACE INTO expansions (key, data, created_at) VALUES (?, ?, ?)",
                (key, expansion.model_dump_json(), time.time()),
            )
            await self._db.commit()
        except (aiosqlite.Error, sqlite3.Error) as exc:
            logger.warning("Cache write error (expansion): %s — continuing without cache", exc)

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def stats(self) -> dict:
        if self._db is None:
            return {"status": "disabled"}
        try:
            async with self._db.execute("SELECT COUNT(*) FROM graphs") as c:
                graph_count = (await c.fetchone())[0]
            async with self._db.execute("SELECT COUNT(*) FROM nodes") as c:
                node_count = (await c.fetchone())[0]
            async with self._db.execute("SELECT COUNT(*) FROM expansions") as c:
                expansion_count = (await c.fetchone())[0]
            return {
                "status": "ok",
                "cached_graphs": graph_count,
                "cached_node_details": node_count,
                "cached_expansions": expansion_count,
            }
        except (aiosqlite.Error, sqlite3.Error) as exc:
            return {"status": "error", "detail": str(exc)}
