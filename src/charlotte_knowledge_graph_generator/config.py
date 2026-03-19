"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"
    cache_db_path: str = "cache.db"
    max_nodes_per_graph: int = 25
    max_nodes_per_expand: int = 12
    rate_limit_per_minute: int = 10
    static_dir: str = "static"
    # Bump this string when prompts change to bust the cache
    prompt_version: str = "v3"
    # Tavily web search — basic search (optional, used for node expansion)
    tavily_api_key: str | None = None
    search_max_results_per_query: int = 5
    search_num_queries: int = 3
    # Tavily Research API — autonomous multi-step research (optional, used for graph generation)
    tavily_research_api_key: str | None = None
    research_timeout_secs: int = 180
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()  # type: ignore[call-arg]  # fails fast at startup if key missing
