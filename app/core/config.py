"""Application configuration loaded from environment variables (12-factor style)."""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Required values have no default on purpose:
    the app refuses to start without a database URL and a JWT secret."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "TaskFlow"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pw@host/db")
    db_pool_size: int = 10
    db_max_overflow: int = 10

    secret_key: str = Field(min_length=32, description="HMAC secret used to sign JWTs")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1)

    # Argon2id cost parameters (library defaults follow RFC 9106 "low memory").
    # They are configurable only so the test-suite can run quickly.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    @model_validator(mode="after")
    def _reject_dev_secret_in_production(self) -> "Settings":
        # docker-compose ships a clearly-labelled development default so that
        # `docker compose up --build` works out of the box. It must never reach production.
        if self.environment.lower() == "production" and self.secret_key.startswith("dev-only"):
            raise ValueError("SECRET_KEY is the development placeholder; set a real secret in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
