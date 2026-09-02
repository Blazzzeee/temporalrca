from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TEMPORALRCA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://temporalrca:temporalrca@postgres:5432/temporalrca"
    database_pool_size: int = Field(default=24, ge=5, le=80)
    database_max_overflow: int = Field(default=8, ge=0, le=40)
    enrollment_token: str = Field(default="development-enrollment-token", min_length=16)
    ground_truth_token: str = Field(default="development-ground-truth-token", min_length=16)
    credential_pepper: str = Field(default="development-only-change-me", min_length=16)
    log_level: str = "INFO"
    raw_retention_hours: int = 24
    rollup_retention_days: int = 7
    rollup_lateness_minutes: int = 15
    inventory_default_lease_seconds: int = 30
    max_compressed_batch_bytes: int = 2 * 1024 * 1024
    normal_batch_events: int = 500
    backfill_batch_events: int = 2_000
    export_directory: str = "/exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
