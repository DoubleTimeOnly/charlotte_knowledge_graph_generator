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
    prompt_version: str = "v1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()  # type: ignore[call-arg]  # fails fast at startup if key missing
