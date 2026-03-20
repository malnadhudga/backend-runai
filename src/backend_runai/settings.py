"""Load configuration from environment (and optional `.env` for local dev)."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str
    # Model used for every /v1/chat call (RunAI default; override only if you know the ID).
    gemini_model: str = "gemini-2.5-flash-lite"
    proxy_bearer_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    cors_origins: str = ""
    log_prompts: bool = False

    @field_validator("log_prompts", mode="before")
    @classmethod
    def parse_log_prompts(cls, value: object) -> bool:
        """Coerce common truthy strings for LOG_PROMPTS."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        normalized = str(value).strip().lower()
        return normalized in ("1", "true", "yes", "on")

    def cors_origin_list(self) -> list[str]:
        """Return stripped non-empty origins from `CORS_ORIGINS`."""
        if not self.cors_origins.strip():
            return []
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]
