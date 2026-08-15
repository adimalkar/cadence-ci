from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CADENCE_", env_file=".env", extra="ignore"
    )

    github_token: str = ""
    database_url: str = "postgresql://localhost/cadence"
    log_store: Path = Path("./data/logs")

    # HMAC-SHA256 secret shared with the GitHub App's webhook config. Required to run
    # the receiver at all -- there is no "insecure dev mode" fallback, because a
    # webhook endpoint that accepts unsigned payloads accepts payloads from anyone.
    webhook_secret: str = ""

    # Current rate card. Bumped when GitHub changes prices -- which happened on
    # 2026-01-01 (hosted runners cut up to 39%) and may happen again, so every finding
    # records the version that produced its dollar figure.
    rate_card_version: int = 2026


settings = Settings()
