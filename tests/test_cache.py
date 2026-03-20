"""Tests for the SQLite CacheLayer."""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from charlotte_knowledge_graph_generator.cache import CacheLayer, _graph_key, _node_key
from charlotte_knowledge_graph_generator.models import NodeType


class TestCacheKeys:
    def test_graph_key_is_deterministic(self):
        k1 = _graph_key("Israel-Palestine conflict", 2, "v1")
        k2 = _graph_key("Israel-Palestine conflict", 2, "v1")
        assert k1 == k2

    def test_graph_key_case_insensitive_topic(self):
        k1 = _graph_key("Topic", 2, "v1")
        k2 = _graph_key("topic", 2, "v1")
        assert k1 == k2

    def test_graph_key_differs_by_depth(self):
        assert _graph_key("t", 1, "v1") != _graph_key("t", 2, "v1")

    def test_graph_key_differs_by_prompt_version(self):
        assert _graph_key("t", 2, "v1") != _graph_key("t", 2, "v2")

    def test_node_key_is_deterministic(self):
        k1 = _node_key("Oslo Accords", "Document", "v1")
        k2 = _node_key("Oslo Accords", "Document", "v1")
        assert k1 == k2

    def test_node_key_case_insensitive_label(self):
        k1 = _node_key("Oslo Accords", "Document", "v1")
        k2 = _node_key("oslo accords", "Document", "v1")
        assert k1 == k2

    def test_node_key_differs_by_type(self):
        assert _node_key("t", "Document", "v1") != _node_key("t", "Person", "v1")


class TestGraphCache:
    async def test_miss_returns_none(self, cache: CacheLayer):
        result = await cache.get_graph("unknown topic", 2, "v1")
        assert result is None

    async def test_set_then_get_roundtrip(
        self, cache: CacheLayer, graph_fixture
    ):
        await cache.set_graph("Israel-Palestine conflict", 2, "v1", graph_fixture)
        retrieved = await cache.get_graph("Israel-Palestine conflict", 2, "v1")
        assert retrieved is not None
        assert retrieved.topic == graph_fixture.topic
        assert len(retrieved.nodes) == len(graph_fixture.nodes)
        assert len(retrieved.edges) == len(graph_fixture.edges)

    async def test_set_overwrites_existing(
        self, cache: CacheLayer, graph_fixture
    ):
        await cache.set_graph("t", 1, "v1", graph_fixture)
        await cache.set_graph("t", 1, "v1", graph_fixture)  # no conflict error
        result = await cache.get_graph("t", 1, "v1")
        assert result is not None

    async def test_different_depth_is_different_entry(
        self, cache: CacheLayer, graph_fixture
    ):
        await cache.set_graph("t", 1, "v1", graph_fixture)
        assert await cache.get_graph("t", 2, "v1") is None

    async def test_different_prompt_version_is_cache_miss(
        self, cache: CacheLayer, graph_fixture
    ):
        await cache.set_graph("t", 2, "v1", graph_fixture)
        assert await cache.get_graph("t", 2, "v2") is None

    async def test_node_fields_preserved_in_roundtrip(
        self, cache: CacheLayer, graph_fixture
    ):
        await cache.set_graph("t", 1, "v1", graph_fixture)
        retrieved = await cache.get_graph("t", 1, "v1")
        original_node = graph_fixture.nodes[0]
        retrieved_node = next(
            n for n in retrieved.nodes if n.id == original_node.id
        )
        assert retrieved_node.label == original_node.label
        assert retrieved_node.type == original_node.type
        assert retrieved_node.era == original_node.era


class TestNodeDetailCache:
    async def test_miss_returns_none(self, cache: CacheLayer):
        result = await cache.get_node_detail("Unknown Node", "Document", "v1")
        assert result is None

    async def test_set_then_get_roundtrip(
        self, cache: CacheLayer, detail_fixture
    ):
        await cache.set_node_detail(
            "Oslo Accords", NodeType.DOCUMENT.value, "v1", detail_fixture
        )
        retrieved = await cache.get_node_detail(
            "Oslo Accords", NodeType.DOCUMENT.value, "v1"
        )
        assert retrieved is not None
        assert retrieved.label == detail_fixture.label
        assert retrieved.summary == detail_fixture.summary
        assert retrieved.key_facts == detail_fixture.key_facts
        assert retrieved.date_range == detail_fixture.date_range
        assert retrieved.sources == detail_fixture.sources

    async def test_different_node_type_is_cache_miss(
        self, cache: CacheLayer, detail_fixture
    ):
        await cache.set_node_detail("X", "Document", "v1", detail_fixture)
        assert await cache.get_node_detail("X", "Person", "v1") is None


class TestCacheStats:
    async def test_empty_stats(self, cache: CacheLayer):
        stats = await cache.stats()
        assert stats["status"] == "ok"
        assert stats["cached_graphs"] == 0
        assert stats["cached_node_details"] == 0

    async def test_stats_reflect_inserts(
        self, cache: CacheLayer, graph_fixture, detail_fixture
    ):
        await cache.set_graph("t1", 1, "v1", graph_fixture)
        await cache.set_graph("t2", 1, "v1", graph_fixture)
        await cache.set_node_detail("Oslo Accords", "Document", "v1", detail_fixture)
        stats = await cache.stats()
        assert stats["cached_graphs"] == 2
        assert stats["cached_node_details"] == 1


class TestCacheSetupFailure:
    async def test_setup_failure_sets_db_to_none(self, tmp_path):
        cache = CacheLayer(str(tmp_path / "x.db"))
        with patch("charlotte_knowledge_graph_generator.cache.aiosqlite.connect", side_effect=sqlite3.Error("disk full")):
            await cache.setup()
        assert cache._db is None

    async def test_cache_is_effectively_disabled_after_setup_failure(self, tmp_path, graph_fixture):
        cache = CacheLayer(str(tmp_path / "x.db"))
        with patch("charlotte_knowledge_graph_generator.cache.aiosqlite.connect", side_effect=sqlite3.Error("disk full")):
            await cache.setup()
        result = await cache.get_graph("t", 1, "v1")
        assert result is None


class TestCacheErrorPaths:
    async def test_get_graph_db_error_returns_none(self, cache: CacheLayer, graph_fixture):
        # Inject a real entry first, then corrupt the read path
        await cache.set_graph("t", 1, "v1", graph_fixture)
        mock_cursor = MagicMock()
        mock_cursor.__aenter__ = AsyncMock(side_effect=sqlite3.Error("read error"))
        mock_cursor.__aexit__ = AsyncMock(return_value=False)
        with patch.object(cache._db, "execute", return_value=mock_cursor):
            result = await cache.get_graph("t", 1, "v1")
        assert result is None

    async def test_set_graph_db_error_does_not_raise(self, cache: CacheLayer, graph_fixture):
        with patch.object(cache._db, "execute", side_effect=sqlite3.Error("write error")):
            # Should not raise — cache errors are swallowed
            await cache.set_graph("t", 1, "v1", graph_fixture)

    async def test_get_node_db_error_returns_none(self, cache: CacheLayer, detail_fixture):
        await cache.set_node_detail("Oslo Accords", "Document", "v1", detail_fixture)
        mock_cursor = MagicMock()
        mock_cursor.__aenter__ = AsyncMock(side_effect=sqlite3.Error("read error"))
        mock_cursor.__aexit__ = AsyncMock(return_value=False)
        with patch.object(cache._db, "execute", return_value=mock_cursor):
            result = await cache.get_node_detail("Oslo Accords", "Document", "v1")
        assert result is None

    async def test_set_node_db_error_does_not_raise(self, cache: CacheLayer, detail_fixture):
        with patch.object(cache._db, "execute", side_effect=sqlite3.Error("write error")):
            await cache.set_node_detail("Oslo Accords", "Document", "v1", detail_fixture)

    async def test_stats_db_error_returns_error_status(self, cache: CacheLayer):
        mock_cursor = MagicMock()
        mock_cursor.__aenter__ = AsyncMock(side_effect=sqlite3.Error("stats error"))
        mock_cursor.__aexit__ = AsyncMock(return_value=False)
        with patch.object(cache._db, "execute", return_value=mock_cursor):
            result = await cache.stats()
        assert result["status"] == "error"


class TestExpansionCache:
    async def test_get_expansion_miss_returns_none(self, cache: CacheLayer):
        result = await cache.get_expansion("Unknown Node", "Document", "v1")
        assert result is None

    async def test_get_set_expansion(self, cache: CacheLayer, subgraph_fixture):
        await cache.set_expansion(
            "Oslo Accords", NodeType.DOCUMENT.value, "v1", subgraph_fixture
        )
        retrieved = await cache.get_expansion(
            "Oslo Accords", NodeType.DOCUMENT.value, "v1"
        )
        assert retrieved is not None
        assert len(retrieved.nodes) == len(subgraph_fixture.nodes)
        assert retrieved.nodes[0].id == subgraph_fixture.nodes[0].id


class TestCacheDisabled:
    async def test_get_graph_returns_none_when_disabled(
        self, tmp_path, graph_fixture
    ):
        cache = CacheLayer(str(tmp_path / "x.db"))
        # Do NOT call setup() — _db remains None
        result = await cache.get_graph("t", 1, "v1")
        assert result is None

    async def test_set_graph_is_noop_when_disabled(
        self, tmp_path, graph_fixture
    ):
        cache = CacheLayer(str(tmp_path / "x.db"))
        # Should not raise
        await cache.set_graph("t", 1, "v1", graph_fixture)

    async def test_get_node_detail_returns_none_when_disabled(self, tmp_path):
        cache = CacheLayer(str(tmp_path / "x.db"))
        result = await cache.get_node_detail("Oslo Accords", "Document", "v1")
        assert result is None

    async def test_set_node_detail_is_noop_when_disabled(self, tmp_path, detail_fixture):
        cache = CacheLayer(str(tmp_path / "x.db"))
        await cache.set_node_detail("Oslo Accords", "Document", "v1", detail_fixture)

    async def test_stats_when_disabled(self, tmp_path):
        cache = CacheLayer(str(tmp_path / "x.db"))
        stats = await cache.stats()
        assert stats["status"] == "disabled"
