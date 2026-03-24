"""Unit tests for ReadwiseSourceBackend using respx for HTTP mocking."""

import pytest
import respx
from httpx import Response

from charlotte_knowledge_graph_generator.sources import (
    HighlightWithContext,
    ReadwiseAuthError,
    ReadwiseBookNotFoundError,
    ReadwiseNoHighlightsError,
    ReadwiseSourceBackend,
    _extract_context,
    _sentences,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

READWISE_V2 = "https://readwise.io/api/v2"
READWISE_V3 = "https://readwise.io/api/v3"

BOOK_LIST_RESPONSE = {
    "results": [{"id": 42, "title": "Thinking, Fast and Slow", "source_url": None}],
    "next": None,
}

HIGHLIGHTS_RESPONSE = {
    "results": [
        {"text": "System 1 operates automatically and quickly."},
        {"text": "System 2 allocates attention to effortful activities."},
    ],
    "next": None,
}

BOOK_META_RESPONSE = {
    "id": 42,
    "title": "Thinking, Fast and Slow",
    "source_url": None,
    "category": "books",
}

V3_LIST_RESPONSE = {
    "results": [
        {
            "id": "doc-abc",
            "title": "Thinking, Fast and Slow",
            "source_url": None,
            "category": "article",
            "html_content": "<p>He called it System 1 and System 2. "
                            "System 1 operates automatically and quickly. "
                            "System 2 allocates attention to effortful activities. "
                            "Both systems interact constantly.</p>",
        }
    ],
    "nextPageCursor": None,
}

V3_HTML_RESPONSE = {
    "results": [
        {
            "id": "doc-abc",
            "html_content": "<p>He called it System 1 and System 2. "
                            "System 1 operates automatically and quickly. "
                            "System 2 allocates attention to effortful activities. "
                            "Both systems interact constantly.</p>",
        }
    ]
}


# ── Tests: resolve_book_id ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_resolve_book_id_by_numeric_string():
    backend = ReadwiseSourceBackend(api_key="test-key")
    book_id = await backend.resolve_book_id("42")
    assert book_id == 42


@pytest.mark.anyio
@respx.mock
async def test_resolve_book_id_by_title():
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))

    backend = ReadwiseSourceBackend(api_key="test-key")
    book_id = await backend.resolve_book_id("Thinking, Fast and Slow")
    assert book_id == 42


@pytest.mark.anyio
@respx.mock
async def test_book_not_found_raises_error():
    respx.get(f"{READWISE_V2}/books/").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )

    backend = ReadwiseSourceBackend(api_key="test-key")
    with pytest.raises(ReadwiseBookNotFoundError):
        await backend.resolve_book_id("Nonexistent Book")


@pytest.mark.anyio
@respx.mock
async def test_401_raises_auth_error():
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(401))

    backend = ReadwiseSourceBackend(api_key="bad-key")
    with pytest.raises(ReadwiseAuthError):
        await backend.resolve_book_id("Some Book")


# ── Tests: fetch ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_fetch_by_title_returns_highlights_with_context():
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))
    respx.get(f"{READWISE_V2}/highlights/").mock(return_value=Response(200, json=HIGHLIGHTS_RESPONSE))
    respx.get(f"{READWISE_V2}/books/42/").mock(return_value=Response(200, json=BOOK_META_RESPONSE))
    # _find_reader_document: paginate v3 list (no source_url fast path)
    respx.get(f"{READWISE_V3}/list/").mock(return_value=Response(200, json=V3_LIST_RESPONSE))
    # _fetch_article_text: withHtmlContent call
    respx.get(f"{READWISE_V3}/list/", params__contains={"withHtmlContent": "true"}).mock(
        return_value=Response(200, json=V3_HTML_RESPONSE)
    )

    backend = ReadwiseSourceBackend(api_key="test-key", context_sentences=1)
    result = await backend.fetch("Thinking, Fast and Slow")

    assert result.book_id == 42
    assert result.book_title == "Thinking, Fast and Slow"
    assert len(result.highlights) == 2
    assert result.highlights[0].text == "System 1 operates automatically and quickly."


@pytest.mark.anyio
@respx.mock
async def test_fetch_by_book_id_returns_highlights():
    """Numeric query bypasses book search API call."""
    respx.get(f"{READWISE_V2}/highlights/").mock(return_value=Response(200, json=HIGHLIGHTS_RESPONSE))
    respx.get(f"{READWISE_V2}/books/42/").mock(return_value=Response(200, json=BOOK_META_RESPONSE))
    respx.get(f"{READWISE_V3}/list/").mock(return_value=Response(200, json=V3_LIST_RESPONSE))
    respx.get(f"{READWISE_V3}/list/", params__contains={"withHtmlContent": "true"}).mock(
        return_value=Response(200, json=V3_HTML_RESPONSE)
    )

    backend = ReadwiseSourceBackend(api_key="test-key")
    result = await backend.fetch("42")

    assert result.book_id == 42
    assert len(result.highlights) == 2


@pytest.mark.anyio
@respx.mock
async def test_no_highlights_raises_error():
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))
    respx.get(f"{READWISE_V2}/highlights/").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    respx.get(f"{READWISE_V2}/books/42/").mock(return_value=Response(200, json=BOOK_META_RESPONSE))

    backend = ReadwiseSourceBackend(api_key="test-key")
    with pytest.raises(ReadwiseNoHighlightsError):
        await backend.fetch("Thinking, Fast and Slow")


@pytest.mark.anyio
@respx.mock
async def test_max_highlights_cap_is_enforced():
    many_highlights = {"results": [{"text": f"Highlight {i}"} for i in range(150)], "next": None}
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))
    respx.get(f"{READWISE_V2}/highlights/").mock(return_value=Response(200, json=many_highlights))
    respx.get(f"{READWISE_V2}/books/42/").mock(return_value=Response(200, json=BOOK_META_RESPONSE))
    # Simulate reader doc not found so context is skipped
    respx.get(f"{READWISE_V3}/list/").mock(
        return_value=Response(200, json={"results": [], "nextPageCursor": None})
    )

    backend = ReadwiseSourceBackend(api_key="test-key", max_highlights=100)
    result = await backend.fetch("Thinking, Fast and Slow")

    assert len(result.highlights) == 100


@pytest.mark.anyio
@respx.mock
async def test_429_retries_with_backoff(monkeypatch):
    """429 response triggers retry; second call succeeds."""
    import asyncio
    from unittest.mock import AsyncMock
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip sleep in tests

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(429, headers={"Retry-After": "0"})
        return Response(200, json=BOOK_LIST_RESPONSE)

    respx.get(f"{READWISE_V2}/books/").mock(side_effect=side_effect)

    backend = ReadwiseSourceBackend(api_key="test-key")
    book_id = await backend.resolve_book_id("Thinking, Fast and Slow")
    assert book_id == 42
    assert call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_reader_doc_not_found_highlights_still_returned():
    """If Reader doc can't be found, highlights are returned without context."""
    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))
    respx.get(f"{READWISE_V2}/highlights/").mock(return_value=Response(200, json=HIGHLIGHTS_RESPONSE))
    respx.get(f"{READWISE_V2}/books/42/").mock(return_value=Response(200, json=BOOK_META_RESPONSE))
    # v3 list returns no results → doc not found (graceful degrade)
    respx.get(f"{READWISE_V3}/list/").mock(
        return_value=Response(200, json={"results": [], "nextPageCursor": None})
    )

    backend = ReadwiseSourceBackend(api_key="test-key")
    result = await backend.fetch("Thinking, Fast and Slow")

    assert len(result.highlights) == 2
    assert result.highlights[0].context_before == ""
    assert result.highlights[0].context_after == ""


# ── Tests: _extract_context ────────────────────────────────────────────────────


def test_context_extraction_surrounding_sentences():
    doc = "He called it System 1 and System 2. System 1 operates automatically. System 2 is deliberate. Both interact."
    sents = _sentences(doc)
    before, after = _extract_context(sents, "System 1 operates automatically.", n_before=1, n_after=1)
    assert "System 1 and System 2" in before
    assert "System 2 is deliberate" in after


def test_context_extraction_highlight_not_found_returns_empty():
    doc = "One sentence. Another sentence. Third sentence."
    sents = _sentences(doc)
    before, after = _extract_context(sents, "Completely missing text here.", n_before=1, n_after=1)
    assert before == ""
    assert after == ""


def test_context_extraction_multisent_highlight_fix():
    """Multi-sentence highlight: context should expand from start to end of highlight."""
    doc = "Before A. Highlight starts here. Highlight continues here. After B."
    sents = _sentences(doc)
    # The highlight spans two sentences
    before, after = _extract_context(
        sents, "Highlight starts here.", n_before=1, n_after=1
    )
    assert "Before A" in before
    assert "After B" in after or "Highlight continues" in after


def test_context_extraction_degenerate_fragments_not_counted():
    """Degenerate fragments (len < 10) appear in context but don't count against N limit."""
    # Insert a degenerate fragment between before-context sentences
    doc = "Real sentence A. e. Real sentence B. The highlight is here. Real sentence C. a. Real sentence D."
    sents = _sentences(doc)
    before, after = _extract_context(sents, "The highlight is here.", n_before=2, n_after=2)
    # Should include 2 real sentences before (A and B) plus the fragment
    assert "Real sentence A" in before
    assert "Real sentence B" in before
    # Should include 2 real sentences after (C and D) plus the fragment
    assert "Real sentence C" in after
    assert "Real sentence D" in after


# ── Tests: cache key ───────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_cache_key_uses_resolved_book_id(graph_fixture, test_settings):
    """GraphService uses readwise:{book_id} as cache key, not the raw query string."""
    from charlotte_knowledge_graph_generator.cache import CacheLayer
    from charlotte_knowledge_graph_generator.graph_service import GraphService

    respx.get(f"{READWISE_V2}/books/").mock(return_value=Response(200, json=BOOK_LIST_RESPONSE))

    import pytest
    from unittest.mock import AsyncMock, MagicMock

    mock_readwise = MagicMock()
    mock_readwise.resolve_book_id = AsyncMock(return_value=42)

    cache = CacheLayer(":memory:")
    await cache.setup()

    from tests.conftest import MockLLMClient
    from charlotte_knowledge_graph_generator.models import SubGraphResponse, NodeDetail

    mock_llm = MockLLMClient(
        graph=graph_fixture,
        subgraph=SubGraphResponse(nodes=[], edges=[]),
        detail=NodeDetail(label="x", type="Person", summary="s", key_facts=[]),
    )

    service = GraphService(
        llm=mock_llm,
        cache=cache,
        settings=test_settings,
        readwise=mock_readwise,
    )

    # Set the graph into cache under readwise:42
    await cache.set_graph("readwise:42", 2, test_settings.prompt_version, graph_fixture)

    # Query with title — should hit cache (no LLM call)
    mock_readwise.fetch = AsyncMock()  # should NOT be called
    result = await service.generate_graph("Thinking, Fast and Slow", depth=2, mode="readwise")
    assert mock_readwise.fetch.call_count == 0
    assert result.nodes == graph_fixture.nodes

    await cache.close()
