from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from tcg_pipeline.embedding_config import DEFAULT_NEWS_EMBEDDING_MODEL, NEWS_EMBEDDING_DIMENSIONS


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    sql_echo: bool = False

    supabase_url: str | None = None
    supabase_project_ref: str | None = None
    database_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    socrata_app_token: str | None = None
    geocodio_api_key: str | None = None
    geocodio_base_url: str = "https://api.geocod.io/v1.9"
    esri_api_key: str | None = None
    esri_geocode_base_url: str = (
        "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer"
    )
    geocoding_timeout_seconds: float = 8.0
    allowed_emails: str | None = None
    redis_url: str | None = None
    scrape_job_queue_name: str = "scrape_jobs"
    scrape_job_timeout_seconds: int = 900
    scrape_job_result_ttl_seconds: int = 86400
    scrape_job_failure_ttl_seconds: int = 604800
    worker_name: str | None = None
    worker_heartbeat_interval_seconds: int = Field(default=30, ge=1)
    worker_health_port: int = Field(default=8081, ge=0, le=65535)
    worker_health_max_age_seconds: int = Field(default=300, ge=1)
    news_scheduler_leader: bool = False
    news_scheduler_interval_seconds: int = Field(default=60, ge=1)
    news_scheduler_catchup_hours: int = Field(default=24, ge=1)
    news_scheduler_jitter_seconds: int = Field(default=300, ge=0)
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ai_gateway_api_key: str | None = None
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    news_triage_model: str = "claude-haiku-4-5-20251001"
    news_triage_provider: str = "anthropic"
    news_triage_max_tokens: int = Field(default=300, ge=1)
    news_extract_model: str = "claude-opus-4-7"
    news_extract_provider: str = "anthropic"
    news_extract_max_tokens: int = Field(default=5000, ge=1)
    news_semantic_model: str = "claude-opus-4-7"
    news_semantic_provider: str = "anthropic"
    news_semantic_max_tokens: int = Field(default=5000, ge=1)
    news_semantic_retry_max_tokens: int = Field(default=10000, ge=1)
    news_llm_timeout_seconds: float = Field(default=90.0, ge=1)
    news_embedding_provider: str = "openai"
    news_embedding_model: str = DEFAULT_NEWS_EMBEDDING_MODEL
    news_embedding_dimensions: int = Field(default=NEWS_EMBEDDING_DIMENSIONS, ge=1)
    news_embedding_batch_size: int = Field(default=32, ge=1)
    news_embedding_max_chars: int = Field(default=12_000, ge=500)
    news_embedding_timeout_seconds: float = Field(default=60.0, ge=1)
    agent_enabled_for_news: bool = True
    agent_enabled_for_permits: bool = True
    agent_allow_live_llm: bool = False
    # Per-profile live-LLM kill switches. Default to None so deployments that
    # set only the global flag keep working unchanged; when set, they override
    # the global for that profile only. See live_llm_allowed_for() below.
    agent_allow_live_llm_news: bool | None = None
    agent_allow_live_llm_permits: bool | None = None
    news_regression_auto_apply_enabled: bool = False
    news_use_legacy_pass3: bool = False
    news_use_legacy_semantic: bool = False
    reset_tools_enabled: bool = False
    reset_backup_dir: Path = Field(default_factory=lambda: Path("data/output/db_snapshots"))
    reset_protected_database_hosts: str = ""
    reset_protected_project_refs: str = ""

    api_cors_origins: str = "http://localhost:3000"
    api_auth_audience: str = "authenticated"
    api_required_role: str = "authenticated"
    api_jwks_cache_ttl_seconds: int = 600

    data_dir: Path = Field(default_factory=lambda: Path("data"))
    seed_dir: Path = Field(default_factory=lambda: Path("data/seed"))
    output_dir: Path = Field(default_factory=lambda: Path("data/output"))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def project_ref(self) -> str | None:
        if self.supabase_project_ref:
            return self.supabase_project_ref
        if not self.supabase_url:
            return None
        parsed = urlparse(self.supabase_url)
        hostname = parsed.hostname or ""
        if not hostname:
            return None
        return hostname.split(".")[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_database_url(self) -> bool:
        return bool(self.database_url)

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Paste the direct Postgres connection string "
                "from Supabase into .env."
            )
        return normalize_database_url(self.database_url)

    def live_llm_allowed_for(self, profile_name: str) -> bool:
        """Resolve whether live LLM calls are allowed for the given agent profile.

        Returns the per-profile override when set; otherwise the global
        ``agent_allow_live_llm`` value. Profile names not explicitly mapped
        fall through to the global setting.
        """
        per_profile: bool | None = {
            "news_v1": self.agent_allow_live_llm_news,
            "permit_v1": self.agent_allow_live_llm_permits,
        }.get(profile_name)
        if per_profile is not None:
            return per_profile
        return self.agent_allow_live_llm


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
