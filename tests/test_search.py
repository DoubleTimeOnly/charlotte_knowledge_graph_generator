"""Unit tests for SearchService.

Mocks httpx.AsyncClient — never hits the real Tavily API.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from charlotte_knowledge_graph_generator.sources import SearchService
from charlotte_knowledge_graph_generator.models import SearchResult


def _make_tavily_response(results: list[dict]) -> MagicMock:
    """Build a fake httpx.Response with Tavily-shaped JSON."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"results": results})
    return resp


def _make_service(max_results: int = 5) -> SearchService:
    return SearchService(api_key="test-key", max_results=max_results)


_RESULT_A = {"title": "Result A", "url": "https://example.com/a", "content": "Snippet A"}
_RESULT_B = {"title": "Result B", "url": "https://example.com/b", "content": "Snippet B"}
_RESULT_C = {"title": "Result C", "url": "https://example.com/c", "content": "Snippet C"}


# ── SearchService.search ───────────────────────────────────────────────────────


class TestSearchHappyPath:
    async def test_returns_results_from_multiple_queries(self):
        service = _make_service()
        response_q1 = _make_tavily_response([_RESULT_A, _RESULT_B])
        response_q2 = _make_tavily_response([_RESULT_C])

        responses = iter([response_q1, response_q2])

        async def mock_post(*args, **kwargs):
            return next(responses)

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=mock_post)
            results = await service.search(["query 1", "query 2"])

        assert len(results) == 3
        assert results[0].url == "https://example.com/a"
        assert results[1].url == "https://example.com/b"
        assert results[2].url == "https://example.com/c"

    async def test_deduplicates_by_url(self):
        """Same URL from two queries should appear only once."""
        service = _make_service()
        response = _make_tavily_response([_RESULT_A])

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=response)
            results = await service.search(["query 1", "query 2"])

        assert len(results) == 1
        assert results[0].url == "https://example.com/a"

    async def test_maps_fields_correctly(self):
        service = _make_service()
        response = _make_tavily_response([_RESULT_A])

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=response)
            results = await service.search(["query"])

        assert results[0].title == "Result A"
        assert results[0].snippet == "Snippet A"


class TestSearchTimeoutFallback:
    async def test_one_query_timeout_returns_partial_results(self):
        """Timeout on one query should still return results from others."""
        service = _make_service()
        response_ok = _make_tavily_response([_RESULT_A])

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("timeout")
            return response_ok

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=mock_post)
            results = await service.search(["failing query", "working query"])

        assert len(results) == 1
        assert results[0].url == "https://example.com/a"


class TestSearchAllFail:
    async def test_all_queries_fail_returns_empty_list(self):
        service = _make_service()

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            results = await service.search(["query 1", "query 2"])

        assert results == []


class TestSearchHttpErrorFallback:
    async def test_429_on_one_query_partial_results(self):
        service = _make_service()
        err_response = MagicMock(spec=httpx.Response)
        err_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "429", request=MagicMock(), response=MagicMock()
        ))
        ok_response = _make_tavily_response([_RESULT_B])

        responses = iter([err_response, ok_response])

        async def mock_post(*args, **kwargs):
            return next(responses)

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=mock_post)
            results = await service.search(["rate-limited", "working"])

        assert len(results) == 1
        assert results[0].url == "https://example.com/b"


class TestSearchZeroResults:
    async def test_empty_results_list_returns_empty(self):
        service = _make_service()
        response = _make_tavily_response([])

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=response)
            results = await service.search(["query"])

        assert results == []


class TestSearchFiltersJavascriptUrls:
    async def test_javascript_url_is_filtered_out(self):
        service = _make_service()
        bad_result = {"title": "Evil", "url": "javascript:evil()", "content": "xss"}
        response = _make_tavily_response([bad_result, _RESULT_A])

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=response)
            results = await service.search(["query"])

        assert len(results) == 1
        assert results[0].url == "https://example.com/a"

    async def test_http_url_is_allowed(self):
        service = _make_service()
        http_result = {"title": "HTTP", "url": "http://example.com/a", "content": "snippet"}
        response = _make_tavily_response([http_result])

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=response)
            results = await service.search(["query"])

        assert len(results) == 1
        assert results[0].url == "http://example.com/a"


# ── SearchService.format_context ──────────────────────────────────────────────


class TestFormatContext:
    def test_numbered_format(self):
        results = [
            SearchResult(title="Title 1", url="https://a.com", snippet="Snippet 1"),
            SearchResult(title="Title 2", url="https://b.com", snippet="Snippet 2"),
        ]
        text = SearchService.format_context(results)
        assert text == "[1] Title 1 — Snippet 1 (https://a.com)\n[2] Title 2 — Snippet 2 (https://b.com)"

    def test_empty_list_returns_fallback_message(self):
        text = SearchService.format_context([])
        assert text == "(no search results available)"
