from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MeetFlow Dev Alignment Sim"
    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_model_agent: str = Field(default="gpt-5.3-codex", alias="LLM_MODEL_AGENT")
    llm_model_synthesis: str = Field(default="gpt-5", alias="LLM_MODEL_SYNTHESIS")
    llm_agent_max_output_tokens: int = Field(default=8192, alias="LLM_AGENT_MAX_OUTPUT_TOKENS")
    llm_synthesis_max_output_tokens: int = Field(default=8192, alias="LLM_SYNTHESIS_MAX_OUTPUT_TOKENS")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")
    openai_pricing_docs_url: str = Field(
        default="https://developers.openai.com/api/docs/pricing?latest-pricing=standard",
        alias="OPENAI_PRICING_DOCS_URL",
    )

    request_timeout_seconds: float = Field(default=45.0, alias="REQUEST_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=3, alias="LLM_MAX_RETRIES")
    llm_retry_backoff_seconds: float = Field(default=1.5, alias="LLM_RETRY_BACKOFF_SECONDS")

    resolver_cache_dir: str = Field(
        default="~/.cache/meetflow-dev-alignment-sim/sources",
        alias="RESOLVER_CACHE_DIR",
    )
    resolver_download_timeout_seconds: float = Field(default=45.0, alias="RESOLVER_DOWNLOAD_TIMEOUT_SECONDS")
    resolver_max_download_bytes: int = Field(default=250_000_000, alias="RESOLVER_MAX_DOWNLOAD_BYTES")
    resolver_max_extract_bytes: int = Field(default=500_000_000, alias="RESOLVER_MAX_EXTRACT_BYTES")
    resolver_max_extract_files: int = Field(default=20_000, alias="RESOLVER_MAX_EXTRACT_FILES")

    # Optional local fallback for demos without API keys.
    mock_llm: bool = Field(default=False, alias="MOCK_LLM")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
