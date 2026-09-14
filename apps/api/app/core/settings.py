from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEV = "dev"
    PROD = "prod"


class AuthProviderName(StrEnum):
    DEV = "dev"
    ENTRA = "entra"


class StorageBackend(StrEnum):
    LOCAL = "local"
    GCS = "gcs"


class Settings(BaseSettings):
    """Single source of truth for configuration. Read once, cached.

    Per-event values do NOT live here — they live in `events.config`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ── app ──────────────────────────────────────────────────────────────
    app_env: AppEnv = AppEnv.DEV
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    secret_key: str

    # ── the one event ────────────────────────────────────────────────────
    event_slug: str = "dryrun"

    # ── datastores ───────────────────────────────────────────────────────
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False
    redis_url: str

    # ── identity ─────────────────────────────────────────────────────────
    auth_provider: AuthProviderName = AuthProviderName.DEV
    allowed_domains: list[str] = Field(default_factory=list)
    session_cookie_name: str = "ug_session"
    csrf_cookie_name: str = "ug_csrf"
    cookie_secure: bool = False
    session_ttl_participant_seconds: int = 28_800  # 8h sliding  (LLD-v1 §8.2)
    session_ttl_staff_seconds: int = 43_200        # 12h fixed   (LLD-v1 §8.2)
    oidc_state_ttl_seconds: int = 600

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    # ── object storage ───────────────────────────────────────────────────
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_bucket: str = "unigate-local"
    storage_endpoint_url: str = "http://fake-gcs:4443"
    storage_public_url: str = "http://localhost:4443"

    # ── email ────────────────────────────────────────────────────────────
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_from: str = "unigate@localhost"

    @model_validator(mode="after")
    def _enforce_environment_guards(self) -> "Settings":
        if self.app_env is AppEnv.PROD:
            if self.auth_provider is AuthProviderName.DEV:
                raise ValueError(
                    "DevAuthProvider is forbidden when APP_ENV=prod. "
                    "Set AUTH_PROVIDER=entra."
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true when APP_ENV=prod (LLD-v2 §13).")
        if self.auth_provider is AuthProviderName.ENTRA and not (
            self.entra_tenant_id and self.entra_client_id and self.entra_client_secret
        ):
            raise ValueError(
                "AUTH_PROVIDER=entra requires ENTRA_TENANT_ID, ENTRA_CLIENT_ID "
                "and ENTRA_CLIENT_SECRET."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
