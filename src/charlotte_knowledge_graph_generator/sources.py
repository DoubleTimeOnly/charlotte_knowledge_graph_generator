"""Source backends for knowledge graph generation.

Three backends (no shared protocol — return types differ):
  SearchService         — basic/advanced search (queries → snippets)
  TavilyResearchBackend — autonomous research  (topic  → synthesized overview + sources)
  ReadwiseSourceBackend — personal highlights  (query  → ReadwiseResult with highlights+context)

Pipeline diagrams:

SearchService.search():
  queries: list[str]
      │
      ▼ asyncio.gather(*[_query(q) for q in queries], return_exceptions=True)
  per-query results (or Exception)
      │
      ▼ flatten + deduplicate by URL + filter non-http(s) URLs
  list[SearchResult]  (may be empty on total failure)

TavilyResearchBackend.research():
  topic: str
      │
      ▼ POST /research  → {request_id, status: "pending"}  (HTTP 201)
      │
      ▼ poll GET /research/{request_id}  every RESEARCH_POLL_INTERVAL_SECS
  {status: "pending"|"in_progress"}  → keep polling
  {status: "failed"}                 → raise RuntimeError
  {status: "completed"}              → {content: str, sources: [{title, url, favicon}]}
      │
      ├── truncate content to MAX_RESEARCH_CHARS
      ├── filter non-http(s) source URLs
      ▼
  tuple[str | None, list[SearchResult]]
      │ on any http/timeout/parse error:
      └── raises — GraphService catches and falls back to LLM-only

ReadwiseSourceBackend.fetch():
  query: str  (title substring or numeric book_id)
      │
      ▼ _resolve_book_id(client, query)   ← 1 cheap API call
  book_id: int
      │
      ▼ _fetch_highlights(client, book_id)
  raw highlights list
      │
      ▼ _fetch_book_metadata(client, book_id)  → title, source_url
      │
      ▼ _find_reader_document(client, title, source_url)  → reader_doc dict
      │
      ▼ _fetch_document_text(client, reader_doc)  → plain text
      │
      ▼ _extract_context(sentences, highlight, n_before, n_after)  per highlight
      │
      ▼ cap to max_highlights
      ▼
  ReadwiseResult(book_id, book_title, highlights[])
      │ errors:
      ├── 401  → ReadwiseAuthError        → GraphService raises 422
      ├── book not found → ReadwiseBookNotFoundError → 422
      ├── no highlights  → ReadwiseNoHighlightsError → 422
      └── timeout/network → re-raises    → GraphService raises 503
"""

import asyncio
import html.parser
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field

import httpx

from charlotte_knowledge_graph_generator.models import SearchResult

logger = logging.getLogger(__name__)

# ── Readwise typed exceptions ──────────────────────────────────────────────────


class ReadwiseAuthError(Exception):
    """Raised when the Readwise API key is invalid (HTTP 401)."""


class ReadwiseBookNotFoundError(Exception):
    """Raised when no Readwise book matches the query."""


class ReadwiseNoHighlightsError(Exception):
    """Raised when a book is found but has no highlights."""


# ── Readwise data types ────────────────────────────────────────────────────────


@dataclass
class HighlightWithContext:
    text: str
    context_before: str  # N sentences before the highlight in the source document
    context_after: str   # N sentences after the highlight in the source document


@dataclass
class ReadwiseResult:
    book_id: int
    book_title: str
    highlights: list[HighlightWithContext] = field(default_factory=list)


# ── Readwise EPUB source URL allowlist (SSRF mitigation) ──────────────────────

_READWISE_EPUB_ALLOWED_DOMAINS = (
    "readwise.io",
    "readwise-assets.s3.amazonaws.com",
)


def _is_allowed_epub_url(url: str) -> bool:
    """Return True only if the URL is from an allowlisted Readwise domain."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in _READWISE_EPUB_ALLOWED_DOMAINS)
    except Exception:
        return False


# ── Readwise helper functions (sync text processing) ──────────────────────────


def _strip_html(html_str: str) -> str:
    """Extract plain text from HTML, inserting spaces at block-level tags."""
    BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}
    SKIP_TAGS = {"script", "style"}

    class _Parser(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in SKIP_TAGS:
                self._skip_depth += 1
            elif tag in BLOCK_TAGS:
                self.parts.append(" ")

        def handle_endtag(self, tag):
            if tag in SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)

        def handle_data(self, data):
            if self._skip_depth == 0:
                self.parts.append(data)

    parser = _Parser()
    parser.feed(html_str)
    return re.sub(r'\s+', ' ', "".join(parser.parts)).strip()


def _parse_epub(epub_bytes: bytes) -> str:
    """Extract plain text from EPUB bytes using stdlib only."""
    zf = zipfile.ZipFile(io.BytesIO(epub_bytes))

    # Find OPF file via META-INF/container.xml
    container_xml = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
    m = re.search(r'full-path="([^"]+\.opf)"', container_xml)
    if not m:
        raise ValueError("Could not find OPF path in container.xml")
    opf_path = m.group(1)
    opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    opf = zf.read(opf_path).decode("utf-8", errors="replace")

    # Build manifest: id → href (html/xhtml items only)
    manifest: dict[str, str] = {}
    for item_m in re.finditer(r'<item\b([^>]+)/>', opf):
        attrs_str = item_m.group(1)
        id_m = re.search(r'\bid="([^"]+)"', attrs_str)
        href_m = re.search(r'\bhref="([^"]+)"', attrs_str)
        mt_m = re.search(r'\bmedia-type="([^"]+)"', attrs_str)
        if not (id_m and href_m):
            continue
        href = href_m.group(1)
        media_type = mt_m.group(1) if mt_m else ""
        if "html" in media_type or href.endswith((".xhtml", ".html")):
            manifest[id_m.group(1)] = href

    # Spine reading order
    spine_ids = re.findall(r'<itemref\b[^>]+\bidref="([^"]+)"', opf)

    chapters = []
    for item_id in spine_ids:
        href = manifest.get(item_id)
        if not href:
            continue
        chapter_path = f"{opf_dir}/{href}" if opf_dir else href
        try:
            chapter_html = zf.read(chapter_path).decode("utf-8", errors="replace")
            chapters.append(_strip_html(chapter_html))
        except KeyError:
            continue

    return " ".join(chapters)


def _sentences(text: str) -> list[str]:
    """Split text into sentences on sentence-ending punctuation."""
    return re.split(r'(?<=[.!?])\s+', text)


def _extract_context(
    sentences: list[str],
    highlight_text: str,
    n_before: int,
    n_after: int,
) -> tuple[str, str]:
    """Extract N sentences before/after a highlight in a document.

    Bug fixes vs sirius version:
    1. Multi-sentence highlights: find start AND end sentence indices that overlap,
       then take n_before before start and n_after after end.
    2. Degenerate fragments (e.g. "e.", "a.", len < 10): included in output context
       but do NOT count against the n_before/n_after sentence limit.

    Returns (context_before, context_after). Returns ("", "") if not found.
    """
    # Step 1: find all sentence indices that overlap with the highlight
    start_idx: int | None = None
    end_idx: int | None = None
    for i, sent in enumerate(sentences):
        sent_stripped = sent.strip()
        if len(sent_stripped) < 10:
            continue  # skip degenerate fragments when scanning for the highlight
        if highlight_text[:60] in sent or sent[:60] in highlight_text:
            if start_idx is None:
                start_idx = i
            end_idx = i

    if start_idx is None:
        return ("", "")

    # Step 2: expand backward from start_idx, counting only non-degenerate sentences
    before_parts: list[str] = []
    count = 0
    i = start_idx - 1
    while i >= 0 and count < n_before:
        s = sentences[i].strip()
        before_parts.insert(0, sentences[i])
        if len(s) >= 10:
            count += 1
        i -= 1

    # Step 3: expand forward from end_idx, counting only non-degenerate sentences
    after_parts: list[str] = []
    count = 0
    i = end_idx + 1
    while i < len(sentences) and count < n_after:
        s = sentences[i].strip()
        after_parts.append(sentences[i])
        if len(s) >= 10:
            count += 1
        i += 1

    return (" ".join(before_parts), " ".join(after_parts))


# ── ReadwiseSourceBackend ──────────────────────────────────────────────────────


class ReadwiseSourceBackend:
    """Fetch Readwise highlights + surrounding context sentences.

    Uses Readwise API v2 (highlights/books) + v3 (Reader document text).

    Single AsyncClient opened per fetch() call; all private helpers receive
    the client as their first argument (one connection pool per fetch).
    """

    READWISE_V2 = "https://readwise.io/api/v2"
    READWISE_V3 = "https://readwise.io/api/v3"

    def __init__(
        self,
        api_key: str,
        context_sentences: int = 3,
        max_highlights: int = 100,
        timeout_secs: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._context_sentences = context_sentences
        self._max_highlights = max_highlights
        self._timeout_secs = timeout_secs

    async def fetch(self, query: str) -> ReadwiseResult:
        """Fetch highlights for a book by title query or numeric ID.

        Raises:
            ReadwiseAuthError: API key rejected (401).
            ReadwiseBookNotFoundError: No matching book found.
            ReadwiseNoHighlightsError: Book exists but has no highlights.
        """
        headers = {"Authorization": f"Token {self._api_key}"}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self._timeout_secs,
        ) as client:
            book_id = await self._resolve_book_id(client, query)
            raw_highlights = await self._fetch_highlights(client, book_id)

            if not raw_highlights:
                raise ReadwiseNoHighlightsError(
                    f"No highlights found for book_id={book_id} (query={query!r})"
                )

            meta = await self._fetch_book_metadata(client, book_id)
            book_title = meta.get("title") or query

            # Try to fetch document text for context extraction; gracefully degrade
            doc_text: str | None = None
            try:
                reader_doc = await self._find_reader_document(
                    client, meta.get("title") or query, meta.get("source_url")
                )
                doc_text = await self._fetch_document_text(client, reader_doc)
            except Exception as exc:
                logger.warning(
                    "Could not fetch Reader document text for book_id=%d: %s — "
                    "highlights will have no context",
                    book_id,
                    exc,
                )

            # Cap highlights before context extraction
            capped = raw_highlights[: self._max_highlights]
            sents = _sentences(doc_text) if doc_text else []

            highlights: list[HighlightWithContext] = []
            for h in capped:
                text = h.get("text", "").strip()
                if not text:
                    continue
                before, after = ("", "")
                if sents:
                    before, after = _extract_context(
                        sents, text, self._context_sentences, self._context_sentences
                    )
                highlights.append(
                    HighlightWithContext(
                        text=text,
                        context_before=before,
                        context_after=after,
                    )
                )

            return ReadwiseResult(
                book_id=book_id,
                book_title=book_title,
                highlights=highlights,
            )

    async def resolve_book_id(self, query: str) -> int:
        """Public helper: resolve a query to a book_id (1 cheap API call).

        Used by GraphService for two-step cache lookup before full fetch.
        """
        headers = {"Authorization": f"Token {self._api_key}"}
        async with httpx.AsyncClient(headers=headers, timeout=self._timeout_secs) as client:
            return await self._resolve_book_id(client, query)

    # ── private helpers ────────────────────────────────────────────────────────

    async def _get(
        self, client: httpx.AsyncClient, url: str, params: dict | None = None
    ) -> httpx.Response:
        """GET with automatic retry on 429, honouring Retry-After."""
        for attempt in range(6):
            resp = await client.get(url, params=params or {})
            if resp.status_code == 401:
                raise ReadwiseAuthError("Invalid Readwise API key (401)")
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("Readwise rate-limited; retrying in %ds (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp  # unreachable; satisfies type checker

    async def _resolve_book_id(self, client: httpx.AsyncClient, query: str) -> int:
        """Resolve a title string or numeric ID string to a Readwise book_id."""
        if query.strip().isdigit():
            return int(query.strip())
        query_lower = query.lower()
        url: str | None = f"{self.READWISE_V2}/books/"
        params: dict = {"search": query}
        while url:
            data = (await self._get(client, url, params=params)).json()
            for result in data.get("results", []):
                if (result.get("title") or "").lower() == query_lower:
                    return result["id"]
            url = data.get("next")
            params = {}
        raise ReadwiseBookNotFoundError(f"No Readwise book found for query: {query!r}")

    async def _fetch_highlights(self, client: httpx.AsyncClient, book_id: int) -> list[dict]:
        """Fetch all highlights for a book (paginated)."""
        all_highlights: list[dict] = []
        url: str | None = f"{self.READWISE_V2}/highlights/"
        params: dict = {"book_id": book_id, "page_size": 1000}
        while url:
            data = (await self._get(client, url, params=params)).json()
            all_highlights.extend(data.get("results", []))
            url = data.get("next")
            params = {}
        return all_highlights

    async def _fetch_book_metadata(self, client: httpx.AsyncClient, book_id: int) -> dict:
        """GET /api/v2/books/{book_id}/ → dict with title, source_url, category."""
        return (await self._get(client, f"{self.READWISE_V2}/books/{book_id}/")).json()

    async def _find_reader_document(
        self, client: httpx.AsyncClient, title: str, source_url: str | None
    ) -> dict:
        """Find a Readwise Reader (v3) document matching the given title/source_url.

        Fast path: if source_url is a Reader-private URL (private://read/<id>),
        the path segment IS the v3 document ID.

        Otherwise: paginate /api/v3/list/ matching source_url (exact) then
        title (case-insensitive) across all pages.
        """
        if source_url and source_url.startswith("private://read/"):
            doc_id = source_url.split("/")[-1]
            results = (
                await self._get(client, f"{self.READWISE_V3}/list/", params={"id": doc_id})
            ).json().get("results", [])
            if results:
                return results[0]

        all_docs: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict = {"page_size": 100}
            if cursor:
                params["pageCursor"] = cursor
            data = (await self._get(client, f"{self.READWISE_V3}/list/", params=params)).json()
            for doc in data.get("results", []):
                if source_url and doc.get("source_url") == source_url:
                    return doc
                all_docs.append(doc)
            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        title_lower = (title or "").lower()
        for doc in all_docs:
            if (doc.get("title") or "").lower() == title_lower:
                return doc
        raise ValueError(
            f"Document {title!r} not found in Readwise Reader. "
            "Ensure the document is imported into Reader."
        )

    async def _fetch_document_text(self, client: httpx.AsyncClient, reader_doc: dict) -> str:
        """Dispatch to article or EPUB fetcher based on doc category."""
        doc_id = reader_doc["id"]
        category = reader_doc.get("category", "")
        if category == "epub":
            return await self._fetch_epub_text(client, doc_id, reader_doc)
        return await self._fetch_article_text(client, doc_id)

    async def _fetch_article_text(self, client: httpx.AsyncClient, doc_id: str) -> str:
        """GET withHtmlContent → plain text."""
        results = (
            await self._get(
                client,
                f"{self.READWISE_V3}/list/",
                params={"id": doc_id, "withHtmlContent": "true"},
            )
        ).json().get("results", [])
        if not results:
            raise ValueError(f"Reader document {doc_id!r} not found")
        return _strip_html(results[0].get("html_content", ""))

    async def _fetch_epub_text(
        self, client: httpx.AsyncClient, doc_id: str, reader_doc: dict
    ) -> str:
        """Fetch EPUB via raw_source_url (SSRF-checked) → parse to plain text."""
        results = (
            await self._get(
                client,
                f"{self.READWISE_V3}/list/",
                params={"id": doc_id, "withRawSourceUrl": "true"},
            )
        ).json().get("results", [])
        if not results:
            raise ValueError(f"Reader document {doc_id!r} not found")
        raw_url = results[0].get("raw_source_url", "")
        if not _is_allowed_epub_url(raw_url):
            raise ValueError(
                f"EPUB raw_source_url is not from an allowed domain: {raw_url!r}"
            )
        epub_resp = await client.get(raw_url)
        epub_resp.raise_for_status()
        return _parse_epub(epub_resp.content)


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_RESEARCH_URL = "https://api.tavily.com/research"

MAX_RESEARCH_CHARS = 100000  # Truncation limit for research overviews injected into prompts
MAX_SNIPPET_CHARS = 400  # Tavily content field can be full article text; keep prompts lean
RESEARCH_POLL_INTERVAL_SECS = 3  # How often to poll for research completion


class SearchService:
    def __init__(self, api_key: str, max_results: int = 5) -> None:
        self._api_key = api_key
        self._max_results = max_results

    async def search(self, queries: list[str]) -> list[SearchResult]:
        """Run queries in parallel; return deduplicated results. Returns [] on total failure."""
        gathered = await asyncio.gather(
            *[self._query(q) for q in queries], return_exceptions=True
        )
        seen_urls: set[str] = set()
        results: list[SearchResult] = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.warning("Search query failed: %s", item)
                continue
            for r in item:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    results.append(r)
        logger.info("Search: %d deduplicated results from %d queries", len(results), len(queries))
        return results

    async def _query(self, query: str) -> list[SearchResult]:
        """Single Tavily search. Raises on error (caught by asyncio.gather)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": self._max_results,
                    "include_raw_content": False,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchResult(
                    title=r["title"],
                    url=r["url"],
                    snippet=r.get("content", ""),
                )
                for r in data.get("results", [])
                if r.get("url", "").startswith(("http://", "https://"))
            ]

    @staticmethod
    def format_context(results: list[SearchResult]) -> str:
        """Format as numbered list: [1] Title — Snippet (URL)"""
        if not results:
            return "(no search results available)"
        lines = [f"[{i + 1}] {r.title} — {r.snippet} ({r.url})" for i, r in enumerate(results)]
        return "\n".join(lines)


class TavilyResearchBackend:
    """Autonomous multi-step research backend using the Tavily Research API.

    research() initiates an async research task, then polls until it completes.

    POST /research  → {request_id}
    GET  /research/{request_id}  → poll until status "completed" or "failed"

    Completed response shape:
        {"status": "completed", "content": "<synthesized prose>", "sources": [{"title": ..., "url": ..., "favicon": ...}]}

    Raises on any error — GraphService catches and falls back to LLM-only.
    """

    def __init__(self, api_key: str, timeout_secs: int = 120) -> None:
        self._api_key = api_key
        self._timeout_secs = timeout_secs

    async def research(self, topic: str) -> tuple[str | None, list[SearchResult]]:
        """Research a topic. Returns (overview, sources) or raises on failure."""
        async with httpx.AsyncClient(timeout=float(self._timeout_secs)) as client:
            # Step 1: initiate the research task
            resp = await client.post(
                TAVILY_RESEARCH_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"input": topic, "model": "mini"},
            )
            resp.raise_for_status()
            request_id = resp.json()["request_id"]
            logger.info("Research initiated: request_id=%s topic=%r", request_id, topic)

            # Step 2: poll until completed or failed
            poll_url = f"{TAVILY_RESEARCH_URL}/{request_id}"
            while True:
                await asyncio.sleep(RESEARCH_POLL_INTERVAL_SECS)
                poll_resp = await client.get(
                    poll_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                poll_resp.raise_for_status()
                data = poll_resp.json()
                status = data.get("status", None)
                logger.debug("Research poll: request_id=%s status=%s", request_id, status)
                if status == "completed":
                    break
                elif status == "failed":
                    raise RuntimeError(f"Tavily research failed for request_id={request_id}")
                elif status is None:
                    raise RuntimeError(f"Unexpected response shape during research polling: {data}")
                # pending / in_progress — keep polling

        overview = data.get("content") or ""
        logger.debug("Raw research overview for topic=%r: %r", topic, overview)
        if len(overview) > MAX_RESEARCH_CHARS:
            logger.warning(
                "Research overview truncated: %d → %d chars for topic=%r",
                len(overview),
                MAX_RESEARCH_CHARS,
                topic,
            )
            overview = overview[:MAX_RESEARCH_CHARS]

        sources = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:MAX_SNIPPET_CHARS],
            )
            for r in data.get("sources", [])
            if r.get("url", "").startswith(("http://", "https://"))
        ]

        overview_or_none = overview if overview else None
        logger.info(
            "Research: completed for topic=%r, overview_len=%d, sources=%d",
            topic,
            len(overview),
            len(sources),
        )
        return overview_or_none, sources
