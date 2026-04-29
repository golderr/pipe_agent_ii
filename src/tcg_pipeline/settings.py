from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    anthropic_api_key: str | None = None
    news_triage_model: str = "claude-haiku-4-5-20251001"
    news_triage_max_tokens: int = Field(default=300, ge=1)
    news_extract_model: str = "claude-opus-4-7"
    news_extract_max_tokens: int = Field(default=2500, ge=1)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
