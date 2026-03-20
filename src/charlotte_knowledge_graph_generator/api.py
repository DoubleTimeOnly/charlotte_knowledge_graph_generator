"""FastAPI application — routes, lifespan, dependency injection, rate limiting."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from charlotte_knowledge_graph_generator.cache import CacheLayer
from charlotte_knowledge_graph_generator.config import settings
from charlotte_knowledge_graph_generator.graph_service import GraphService
from charlotte_knowledge_graph_generator.llm import (
    AnthropicLLMClient,
    GraphGenerationError,
    LLMRefusalError,
)
from charlotte_knowledge_graph_generator.models import (
    ExpandRequest,
    GraphRequest,
    GraphResponse,
    NodeDetail,
    NodeDetailRequest,
)
from charlotte_knowledge_graph_generator.sources import SearchService, TavilyResearchBackend

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)

# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)


# ── App lifespan (startup / shutdown) ─────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = CacheLayer(settings.cache_db_path)
    await cache.setup()

    llm_client = AnthropicLLMClient(
        client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
        model=settings.anthropic_model,
    )

    research_backend: TavilyResearchBackend | None = None
    if settings.tavily_research_api_key:
        research_backend = TavilyResearchBackend(
            api_key=settings.tavily_research_api_key,
            timeout_secs=settings.research_timeout_secs,
        )
        logger.info("Tavily Research API enabled (timeout=%ds)", settings.research_timeout_secs)

    search_service: SearchService | None = None
    if settings.tavily_api_key:
        search_service = SearchService(
            api_key=settings.tavily_api_key,
            max_results=settings.search_max_results_per_query,
        )
        logger.info("Tavily search enabled (max_results=%d)", settings.search_max_results_per_query)

    if not research_backend and not search_service:
        raise RuntimeError(
            "No Tavily API keys configured. Set TAVILY_RESEARCH_API_KEY or TAVILY_API_KEY."
        )

    service = GraphService(
        llm=llm_client,
        cache=cache,
        settings=settings,
        search=search_service,
        research_backend=research_backend,
    )

    app.state.cache = cache
    app.state.service = service

    yield

    await cache.close()


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Charlotte Knowledge Graph Generator", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_static_dir = Path(settings.static_dir)
if not _static_dir.exists():
    raise RuntimeError(
        f"STATIC_DIR '{settings.static_dir}' does not exist. "
        "Create the directory or set STATIC_DIR correctly."
    )
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Dependency ────────────────────────────────────────────────────────────────


def get_service(request: Request) -> GraphService:
    return request.app.state.service


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    index = _static_dir / "index.html"
    return HTMLResponse(content=index.read_text())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> dict:
    return {
        "search_enabled": settings.tavily_api_key is not None,
        "research_mode": settings.tavily_research_api_key is not None,
    }


@app.get("/admin/cache/stats")
async def cache_stats(request: Request) -> dict:
    return await request.app.state.cache.stats()


@app.post("/api/graph", response_model=GraphResponse)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def generate_graph(
    request: Request,
    body: GraphRequest,
    service: GraphService = Depends(get_service),
) -> GraphResponse:
    try:
        return await service.generate_graph(body.topic, body.depth, force_refresh=body.force_refresh)
    except LLMRefusalError as exc:
        logger.info("LLM refused topic=%r: %s", body.topic, exc)
        raise HTTPException(status_code=422, detail="Topic not supported or too sensitive for graph generation")
    except GraphGenerationError as exc:
        logger.error("Graph generation error for topic=%r: %s", body.topic, exc)
        raise HTTPException(status_code=503, detail="Could not generate graph — please try again")
    except anthropic.AuthenticationError:
        logger.error("Anthropic authentication failed — check ANTHROPIC_API_KEY")
        raise HTTPException(status_code=503, detail="Service unavailable")
    except (anthropic.APITimeoutError, anthropic.RateLimitError) as exc:
        logger.error("LLM exhausted retries for topic=%r: %s", body.topic, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable — please try again later")


@app.post("/api/expand", response_model=GraphResponse)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def expand_node(
    request: Request,
    body: ExpandRequest,
    service: GraphService = Depends(get_service),
) -> GraphResponse:
    # The client sends the full current graph so we can merge server-side.
    # We rebuild a minimal GraphResponse from the request for the merge.
    # Expansion does not require the full graph — it only needs context_nodes labels.
    # We return the new sub-graph only; the client merges it.
    try:
        from charlotte_knowledge_graph_generator.models import GraphNode
        # stub_graph contains seed nodes (origin + neighbors); _merge_graphs treats them as existing
        stub_nodes = body.seed_nodes if body.seed_nodes else [
            GraphNode(id=body.node_id, label=body.node_label, type=body.node_type, description="")
        ]
        stub_graph = GraphResponse(nodes=stub_nodes, edges=[], topic=body.node_label)
        merged = await service.expand_node(
            node_label=body.node_label,
            node_type=body.node_type,
            context_nodes=body.context_nodes,
            current_graph=stub_graph,
        )
        # Return only new nodes (client already has seed nodes) + all new edges
        seed_ids = {n.id for n in stub_nodes}
        new_nodes = [n for n in merged.nodes if n.id not in seed_ids]
        return GraphResponse(nodes=new_nodes, edges=merged.edges, topic=body.node_label)
    except LLMRefusalError as exc:
        logger.info("LLM refused expand for node=%r: %s", body.node_label, exc)
        raise HTTPException(status_code=422, detail="Cannot expand this node")
    except GraphGenerationError as exc:
        logger.error("Expand error for node=%r: %s", body.node_label, exc)
        raise HTTPException(status_code=503, detail="Could not expand node — please try again")
    except (anthropic.APITimeoutError, anthropic.RateLimitError) as exc:
        logger.error("LLM exhausted retries for expand node=%r: %s", body.node_label, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@app.post("/api/node/detail", response_model=NodeDetail)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def get_node_detail(
    request: Request,
    body: NodeDetailRequest,
    service: GraphService = Depends(get_service),
) -> NodeDetail:
    try:
        return await service.get_node_detail(
            label=body.label,
            node_type=body.node_type,
            context_nodes=body.context_nodes,
        )
    except LLMRefusalError as exc:
        logger.info("LLM refused node detail for label=%r: %s", body.label, exc)
        raise HTTPException(status_code=422, detail="Cannot retrieve details for this node")
    except GraphGenerationError as exc:
        logger.error("Node detail error for label=%r: %s", body.label, exc)
        raise HTTPException(status_code=503, detail="Could not load node details — please try again")
    except (anthropic.APITimeoutError, anthropic.RateLimitError) as exc:
        logger.error("LLM exhausted retries for node detail label=%r: %s", body.label, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
