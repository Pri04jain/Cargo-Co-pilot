"""
Central configuration using Pydantic Settings.
All values come from environment variables or .env file — no hardcoded secrets.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://cargo:cargo_secret@db:5432/cargo_copilot"
    database_url_sync: str = "postgresql://cargo:cargo_secret@db:5432/cargo_copilot"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # ── Auth ──────────────────────────────────────────────────────────────────
    secret_key: str = "change_me_to_a_long_random_string_in_production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # ── File storage ──────────────────────────────────────────────────────────
    upload_dir: str = "/data/uploads"
    max_upload_size_mb: int = 50
    allowed_extensions: str = "pdf,png,jpg,jpeg,tiff"

    # ── LLM provider ──────────────────────────────────────────────────────────
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60

    # ── Langfuse observability ────────────────────────────────────────────────
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ── App ───────────────────────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def parse_extensions(cls, v: str) -> str:
        # Accept comma-separated string or already parsed list
        return v if isinstance(v, str) else ",".join(v)

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.allowed_extensions.split(",")]

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# Singleton — import this everywhere instead of constructing a new Settings()
settings = Settings()
