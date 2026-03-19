"""Unit tests for TavilyResearchBackend.

Mocks httpx.AsyncClient — never hits the real Tavily Research API.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from charlotte_knowledge_graph_generator.sources import TavilyResearchBackend


def _make_initiation_response(request_id: str = "test-request-id") -> MagicMock:
    """Build a fake httpx.Response for POST /research (HTTP 201)."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"request_id": request_id, "status": "pending"})
    return resp


def _make_poll_response(status: str, content: str = "", sources: list[dict] | None = None) -> MagicMock:
    """Build a fake httpx.Response for GET /research/{request_id}."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    data: dict = {"status": status}
    if status == "completed":
        data["content"] = content
        data["sources"] = sources or []
    resp.json = MagicMock(return_value=data)
    return resp


def _make_backend(timeout_secs: int = 30) -> TavilyResearchBackend:
    return TavilyResearchBackend(api_key="test-key", timeout_secs=timeout_secs)


_SOURCE_A = {"title": "Source A", "url": "https://example.com/a", "favicon": "https://example.com/favicon.ico"}
_SOURCE_B = {"title": "Source B", "url": "https://example.com/b", "favicon": "https://example.com/favicon.ico"}


# ── TavilyResearchBackend.research ────────────────────────────────────────────


class TestResearchHappyPath:
    async def test_returns_overview_and_sources(self):
        backend = _make_backend()
        init_resp = _make_initiation_response("req-123")
        completed_resp = _make_poll_response("completed", "Detailed overview.", [_SOURCE_A, _SOURCE_B])

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(return_value=completed_resp)
            overview, sources = await backend.research("some topic")

        instance.post.assert_called_once()
        _, call_kwargs = instance.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer test-key"}
        assert call_kwargs["json"] == {"input": "some topic", "model": "mini"}

        instance.get.assert_called_once()
        get_url = instance.get.call_args[0][0]
        assert get_url == "https://api.tavily.com/research/req-123"

        assert overview == "Detailed overview."
        assert len(sources) == 2
        assert sources[0].url == "https://example.com/a"
        assert sources[0].title == "Source A"

    async def test_polls_multiple_times_before_completion(self):
        """Should keep polling through pending/in_progress until completed."""
        backend = _make_backend()
        init_resp = _make_initiation_response()
        pending_resp = _make_poll_response("in_progress")
        completed_resp = _make_poll_response("completed", "Final answer.", [_SOURCE_A])

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(side_effect=[pending_resp, pending_resp, completed_resp])
            overview, sources = await backend.research("some topic")

        assert instance.get.call_count == 3
        assert overview == "Final answer."
        assert len(sources) == 1

    async def test_empty_content_returns_none_overview(self):
        """Empty string content should be returned as None, not an empty string."""
        backend = _make_backend()
        init_resp = _make_initiation_response()
        completed_resp = _make_poll_response("completed", "", [_SOURCE_A])

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(return_value=completed_resp)
            overview, sources = await backend.research("some topic")

        assert overview is None
        assert len(sources) == 1

    async def test_failed_status_raises(self):
        """A 'failed' poll status should raise RuntimeError."""
        backend = _make_backend()
        init_resp = _make_initiation_response()
        failed_resp = _make_poll_response("failed")

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(return_value=failed_resp)
            with pytest.raises(RuntimeError, match="Tavily research failed"):
                await backend.research("some topic")

    async def test_http_error_on_initiation_raises(self):
        """HTTP errors on POST should propagate — GraphService catches and falls back."""
        backend = _make_backend()
        err_response = MagicMock(spec=httpx.Response)
        err_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock())
        )

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=err_response)
            with pytest.raises(httpx.HTTPStatusError):
                await backend.research("some topic")

    async def test_http_error_on_poll_raises(self):
        """HTTP errors during polling should propagate."""
        backend = _make_backend()
        init_resp = _make_initiation_response()
        err_response = MagicMock(spec=httpx.Response)
        err_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())
        )

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(return_value=err_response)
            with pytest.raises(httpx.HTTPStatusError):
                await backend.research("some topic")

    async def test_timeout_raises(self):
        """Timeout should propagate — GraphService catches and falls back."""
        backend = _make_backend()

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            with pytest.raises(httpx.TimeoutException):
                await backend.research("some topic")

    async def test_filters_non_http_urls(self):
        """javascript: and other non-http(s) URLs should be excluded from sources."""
        backend = _make_backend()
        sources = [
            {"title": "Evil", "url": "javascript:evil()", "favicon": ""},
            _SOURCE_A,
        ]
        init_resp = _make_initiation_response()
        completed_resp = _make_poll_response("completed", "overview", sources)

        with patch("httpx.AsyncClient") as MockClient, patch(
            "charlotte_knowledge_graph_generator.sources.asyncio.sleep", new_callable=AsyncMock
        ):
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=init_resp)
            instance.get = AsyncMock(return_value=completed_resp)
            _, result_sources = await backend.research("topic")

        assert len(result_sources) == 1
        assert result_sources[0].url == "https://example.com/a"
