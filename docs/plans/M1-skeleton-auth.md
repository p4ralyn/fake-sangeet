# M1 — Skeleton + Auth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docker compose up` yields a working localhost stack where a seeded OWNER and an unseeded VISITOR can both log in through a fake auth provider and `GET /api/v1/me` returns the correct roles.

**Architecture:** FastAPI + Jinja (no Next.js; v2 §8.2). Thin routes, fat services. Async SQLAlchemy 2.0 + asyncpg, Alembic for every schema change. Opaque Redis-backed sessions with double-submit CSRF. Authentication sits behind an `AuthProvider` protocol with two implementations — `DevAuthProvider` (impersonate any seeded NetID, local only) and `EntraAuthProvider` (real OIDC + PKCE) — selected by a settings enum that hard-fails if the dev provider is ever paired with `APP_ENV=prod`. Storage and mail sit behind their own thin seams so a later cloud migration is a settings change, not a rewrite.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, pydantic-settings, redis-py (async), structlog, PyJWT[crypto], httpx, Jinja2, aiosmtplib, google-cloud-storage, uuid-utils, pytest + pytest-asyncio, uv, Docker Compose.

**Spec:** `docs/LLD-v2.md` §11 (M1 row), §2 (roles), §13 (security floor); `docs/LLD-v2-addendum-A.md` §A1 (`event_inventory`); `docs/LLD-v1.md` §8 (auth + sessions), §12 (schema), §13.1 (auth/me API), §21 (configuration). `CLAUDE.md` is binding throughout.

## Global Constraints

Copied verbatim from `CLAUDE.md` and the Phase 0 decisions. Every task's requirements implicitly include this section.

- **LOCALHOST ONLY.** Zero cloud credentials required to run or test anything. Local postgres, local redis, mailpit for email, fake-gcs-server for object storage.
- **Do NOT create:** Dockerfiles targeting Cloud Run, Terraform, deploy scripts, CI config, GCP resource definitions, Kubernetes, multi-tenancy plumbing, RLS, PgBouncer, materialised views, Cloud Tasks, staging environments. If a task appears to need one, STOP and report instead of adding it.
- **Thin routes, fat services.** `app/api/v1/routes/*` validate → authorize → call a service → shape a response. All domain logic in `app/services/`. Services never import FastAPI. Models never import services. A route file over ~120 lines means logic leaked upward.
- **Async all the way down.** No blocking I/O in a request path. Blocking library calls go through `anyio.to_thread.run_sync`.
- **Pydantic at every boundary.** No bare dicts crossing a module boundary.
- **Alembic for every schema change.** Autogenerate then hand-edit. Expand/contract only.
- **Derive gates dynamically.** Never hardcode a "known values" set in a validator; compute it from the source of truth at import time.
- **Never log PII.** Hash emails, NetIDs and QR tokens.
- **Never f-string SQL.** Enforced by ruff rule `S608`.
- **`event_id` on every table.** Keeps the multi-event seam alive without building multi-tenancy.
- **Complete, drop-in files.** No diffs, no fragments, no "...rest unchanged".
- **Surgical edits.** Change what was asked; do not reformat or reorganise around it.

### Locked decisions (Phase 0, approved)

| ID | Decision |
|---|---|
| 2.1 | `staff_role` enum is `('OWNER','ADMIN','SECURITY')`. `PARTICIPANT` / `VISITOR` are derived at request time and never stored. This replaces the v1 §12 five-value enum. |
| 2.2 | `events.config` follows addendum §A1 (`capacity`, `ticket_types`, `hold_ttl_minutes`, `hold_grace_minutes`). No `phases[]` array. `phase_id` is a plain cohort marker: `1` = offline/sheet, `2` = self-serve. |
| 2.3 | `hard_cap` is dropped. `event_inventory.seats_total` is the single capacity number. |
| 2.4 | `GET /api/v1/me` returns `{user_id, net_id, display_name, email, roles[], event{id,slug,name}, csrf_token}`. |
| — | UUIDv7 generated app-side via `uuid-utils`. No `pg_uuidv7` extension, no custom Postgres image. |
| — | `allowed_domains` lives in `.env` (tenant-level), not `events.config`. |
| — | htmx + Jinja for participant and admin UI; one Vite SPA for the scanner at M6 only. |
| — | `registrations` will be keyed `(event_id, user_id)` with a nullable `ticket_id` when it lands at M2/M4. No M1 code depends on this; recorded so the M2 author does not re-litigate it. |

### Explicitly out of M1 scope

Listed so an executor does not "helpfully" add them:

- No arq worker container. Nothing to run until M3 (email) / M4 (OCR). Adding it now is an idle container.
- No OCR seam. Zero callers until M4. A Protocol with no implementation and no test is scaffolding, not a seam.
- No `inventory_ledger` table and no ledger trigger. The first seat moves at M5; that is where the enforcement lands.
- No `tickets`, `orders`, `registrations`, `passes`, `scans`, `payment_intents`. M2 onward.
- No append-only `scans` trigger / second DB role. Lands with `scans` at M2.

---

## File Structure

```
unigate/
├── CLAUDE.md                                  (exists)
├── Makefile                                   T1  up/down/logs/migrate/seed/test/lint
├── docker-compose.yml                         T1  postgres redis mailpit fake-gcs api
├── .env.example                               T1  every setting, dev defaults
├── .gitignore  .dockerignore                  T1
├── docs/
│   ├── LLD-v1.md  LLD-v2.md  LLD-v2-addendum-A.md   (exist)
│   └── plans/M1-skeleton-auth.md              (this file)
├── scripts/
│   └── seed.py                                T3  event + inventory + staff, idempotent
└── apps/api/
    ├── pyproject.toml                         T1  uv deps, ruff, pytest config
    ├── Dockerfile                             T1  DEV image only
    ├── alembic.ini                            T2
    ├── alembic/
    │   ├── env.py                             T2  async, reads Settings
    │   ├── script.py.mako                     T2
    │   └── versions/0001_core_tables.py       T2  extensions, enums, 7 tables
    ├── app/
    │   ├── main.py                            T1  factory, lifespan, middleware, routers
    │   ├── core/
    │   │   ├── settings.py                    T1  Settings + prod guards
    │   │   ├── logging.py                     T1  structlog + PII redaction
    │   │   ├── errors.py                      T1  ErrorCode enum, AppError, envelope
    │   │   ├── security.py                    T4  token gen, constant-time compare
    │   │   └── deps.py                        T4/T6  get_db, get_redis, session, roles
    │   ├── db/
    │   │   ├── base.py                        T2  DeclarativeBase, mixins
    │   │   └── session.py                     T2  engine, sessionmaker
    │   ├── models/
    │   │   ├── event.py                       T2  Event, Gate, EventInventory
    │   │   ├── user.py                        T2  User, EventStaff
    │   │   └── audit.py                       T2  AuditLog, FunnelEvent
    │   ├── schemas/
    │   │   ├── common.py                      T1  ErrorEnvelope
    │   │   └── me.py                          T6  MeResponse, EventSummary
    │   ├── api/v1/routes/
    │   │   ├── health.py                      T1/T2  /healthz /readyz
    │   │   ├── auth.py                        T5  start, callback, logout, dev picker
    │   │   └── me.py                          T6  /api/v1/me
    │   ├── services/
    │   │   ├── identity/
    │   │   │   ├── provider.py                T5  AuthProvider Protocol, IdentityClaims
    │   │   │   ├── dev.py                     T5  DevAuthProvider
    │   │   │   ├── entra.py                   T7  EntraAuthProvider, JWKS cache
    │   │   │   ├── sessions.py                T4  Redis opaque sessions
    │   │   │   ├── users.py                   T5  upsert_user, staff backfill
    │   │   │   └── roles.py                   T6  Role enum, resolve_roles
    │   │   ├── storage/
    │   │   │   ├── provider.py                T8  ObjectStore Protocol
    │   │   │   ├── local.py                   T8  fake-gcs, unsigned URLs
    │   │   │   └── gcs.py                     T8  real GCS, V4 signed URLs
    │   │   ├── mailer/
    │   │   │   ├── provider.py                T8  Mailer Protocol
    │   │   │   └── smtp.py                    T8  one impl: mailpit AND Workspace
    │   │   └── audit.py                       T6  write_audit, write_funnel_event
    │   └── templates/
    │       ├── base.html                      T5
    │       └── login.html                     T5  Microsoft button + dev NetID picker
    └── tests/
        ├── conftest.py                        T1→T4  settings, db, redis, client
        ├── test_settings.py                   T1
        ├── test_logging_redaction.py          T1
        ├── test_health.py                     T1/T2
        ├── test_migrations.py                 T2  incl. no_oversell CHECK
        ├── test_seed.py                       T3
        ├── test_sessions.py                   T4
        ├── test_auth_dev.py                   T5
        ├── test_me.py                         T6
        ├── test_auth_entra.py                 T7
        └── test_seams.py                      T8
```

Task ordering is strictly sequential: each task's tests must pass before the next begins (CLAUDE.md build order).

---

## Task 1: Scaffold, settings, safe logging, `/healthz`

**Deliverable:** `docker compose up` starts postgres, redis, mailpit, fake-gcs and the API; `curl localhost:8000/healthz` returns `{"status":"ok"}`; logs never emit a raw email or NetID; booting with `APP_ENV=prod AUTH_PROVIDER=dev` refuses to start.

**Files:**
- Create: `.gitignore`, `.dockerignore`, `.env.example`, `Makefile`, `docker-compose.yml`
- Create: `apps/api/pyproject.toml`, `apps/api/Dockerfile`
- Create: `apps/api/app/__init__.py`, `apps/api/app/core/__init__.py`, `apps/api/app/api/__init__.py`, `apps/api/app/api/v1/__init__.py`, `apps/api/app/api/v1/routes/__init__.py`, `apps/api/app/schemas/__init__.py`, `apps/api/app/services/__init__.py`
- Create: `apps/api/app/core/settings.py`, `apps/api/app/core/logging.py`, `apps/api/app/core/errors.py`
- Create: `apps/api/app/schemas/common.py`, `apps/api/app/api/v1/routes/health.py`, `apps/api/app/main.py`
- Test: `apps/api/tests/conftest.py`, `apps/api/tests/test_settings.py`, `apps/api/tests/test_logging_redaction.py`, `apps/api/tests/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `app.core.settings.Settings`, `get_settings() -> Settings` (`@lru_cache`), enums `AppEnv`, `AuthProviderName`
  - `app.core.logging.configure_logging() -> None`, `hash_pii(value: object) -> str`, `redact_pii(logger, method, event_dict) -> dict`
  - `app.core.errors.ErrorCode` (StrEnum), `AppError(code, message, *, http_status=400, details=None)`, `register_error_handlers(app: FastAPI) -> None`
  - `app.main.create_app() -> FastAPI`

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/conftest.py`:

```python
import os
import pytest

TEST_ENV = {
    "APP_ENV": "dev",
    "SECRET_KEY": "test-secret-key-not-for-production-use",
    "DATABASE_URL": "postgresql+asyncpg://unigate:unigate@postgres:5432/unigate_test",
    "REDIS_URL": "redis://redis:6379/1",
    "AUTH_PROVIDER": "dev",
    "ALLOWED_DOMAINS": '["university.edu"]',
    "EVENT_SLUG": "dryrun",
}


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    """Every test starts from a known-good dev configuration."""
    from app.core.settings import get_settings

    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(base_env):
    from app.core.settings import get_settings

    return get_settings()
```

`apps/api/tests/test_settings.py`:

```python
import pytest
from pydantic import ValidationError

from app.core.settings import AppEnv, AuthProviderName, get_settings


def test_dev_defaults_load(settings):
    assert settings.app_env is AppEnv.DEV
    assert settings.auth_provider is AuthProviderName.DEV
    assert settings.allowed_domains == ["university.edu"]


def test_dev_provider_is_refused_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AUTH_PROVIDER", "dev")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="DevAuthProvider is forbidden"):
        get_settings()


def test_insecure_cookies_refused_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        get_settings()


def test_entra_requires_credentials(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="requires ENTRA_"):
        get_settings()
```

`apps/api/tests/test_logging_redaction.py`:

```python
from app.core.logging import hash_pii, redact_pii


def test_known_pii_keys_are_hashed():
    out = redact_pii(None, "info", {"event": "login", "net_id": "aarav.s", "roles": ["OWNER"]})
    assert out["net_id"].startswith("sha256:")
    assert "aarav.s" not in out["net_id"]
    assert out["roles"] == ["OWNER"]


def test_emails_are_hashed_even_in_unknown_keys():
    out = redact_pii(None, "info", {"event": "bounce", "detail": "delivery to aarav@university.edu failed"})
    assert "aarav@university.edu" not in out["detail"]
    assert "sha256:" in out["detail"]
    assert "delivery to" in out["detail"]


def test_none_values_survive():
    assert redact_pii(None, "info", {"email": None})["email"] is None


def test_hash_is_stable_and_truncated():
    assert hash_pii("aarav.s") == hash_pii("aarav.s")
    assert hash_pii("aarav.s") != hash_pii("aarav.t")
    assert len(hash_pii("x")) == len("sha256:") + 16
```

`apps/api/tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_healthz_is_ok(base_env):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm api pytest tests/ -v`
Expected: collection errors — `ModuleNotFoundError: No module named 'app.core.settings'`.

- [ ] **Step 3: Write `apps/api/pyproject.toml`**

```toml
[project]
name = "unigate-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "redis>=5.2",
    "httpx>=0.28",
    "pyjwt[crypto]>=2.10",
    "jinja2>=3.1",
    "python-multipart>=0.0.20",
    "structlog>=24.4",
    "uuid-utils>=0.10",
    "aiosmtplib>=3.0",
    "google-cloud-storage>=2.19",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S", "ASYNC"]
# S608 = hardcoded SQL expression. CLAUDE.md: never f-string SQL.
ignore = ["S101"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S105", "S106"]
"alembic/*" = ["E501"]
```

- [ ] **Step 4: Write `apps/api/app/core/settings.py`**

```python
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
```

- [ ] **Step 5: Write `apps/api/app/core/logging.py`**

```python
import hashlib
import logging
import re
from typing import Any

import structlog

from app.core.settings import get_settings

# Keys whose values are PII and must never reach a log line.
PII_KEYS = frozenset(
    {
        "email", "to_email", "purchaser_email",
        "net_id", "netid", "bound_net_id", "actor_net_id", "net_id_hint",
        "phone", "payer_phone", "payer_name", "display_name", "full_name",
        "utr", "token", "id_token", "qr_token", "access_token", "refresh_token",
        "code", "code_verifier", "client_secret", "password", "secret_key",
        "ms_object_id", "subject", "session_id", "sid", "claim_code",
    }
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def hash_pii(value: object) -> str:
    """Stable, non-reversible, log-safe rendering of a PII value."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def redact_pii(_logger: Any, _method: str, event_dict: dict) -> dict:
    """structlog processor. Hashes known PII keys and any inline email address."""
    for key, value in list(event_dict.items()):
        if value is None:
            continue
        if key.lower() in PII_KEYS:
            event_dict[key] = hash_pii(value)
        elif isinstance(value, str) and _EMAIL_RE.search(value):
            event_dict[key] = _EMAIL_RE.sub(lambda m: hash_pii(m.group()), value)
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = logging.getLevelNamesMapping()[settings.log_level.upper()]
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

- [ ] **Step 6: Write `apps/api/app/core/errors.py` and `apps/api/app/schemas/common.py`**

`app/schemas/common.py`:

```python
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    """The single error shape for the whole API (LLD-v1 §13)."""

    error: ErrorDetail
```

`app/core/errors.py`:

```python
from enum import StrEnum

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorDetail, ErrorEnvelope

log = structlog.get_logger()


class ErrorCode(StrEnum):
    """Stable contract. Extended per milestone; never renumbered."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CSRF_INVALID = "CSRF_INVALID"
    AUTH_PROVIDER_DISABLED = "AUTH_PROVIDER_DISABLED"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    OIDC_STATE_INVALID = "OIDC_STATE_INVALID"
    OIDC_TOKEN_INVALID = "OIDC_TOKEN_INVALID"
    UNKNOWN_NET_ID = "UNKNOWN_NET_ID"
    EVENT_NOT_CONFIGURED = "EVENT_NOT_CONFIGURED"
    INTERNAL = "INTERNAL"


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details


def _envelope(code: str, message: str, request: Request, details: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details, request_id=request_id)
    )
    status_code = getattr(request.state, "_error_status", status.HTTP_400_BAD_REQUEST)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        request.state._error_status = exc.http_status
        log.warning("app_error", code=exc.code.value, path=request.url.path)
        return _envelope(exc.code.value, exc.message, request, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state._error_status = status.HTTP_422_UNPROCESSABLE_ENTITY
        return _envelope(
            ErrorCode.VALIDATION_ERROR.value,
            "Request validation failed.",
            request,
            {"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request.state._error_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        log.exception("unhandled_error", path=request.url.path)
        return _envelope(ErrorCode.INTERNAL.value, "Something went wrong.", request)
```

- [ ] **Step 7: Write `apps/api/app/api/v1/routes/health.py` and `apps/api/app/main.py`**

`app/api/v1/routes/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness. Must not touch any dependency."""
    return {"status": "ok"}
```

`app/main.py`:

```python
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.api.v1.routes import health
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.settings import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("startup", env=get_settings().app_env.value)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title="unigate",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env.value == "dev" else None,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response

    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 8: Write the empty `__init__.py` files**

```bash
cd apps/api
touch app/__init__.py app/core/__init__.py app/db/__init__.py app/models/__init__.py \
      app/schemas/__init__.py app/services/__init__.py app/api/__init__.py \
      app/api/v1/__init__.py app/api/v1/routes/__init__.py tests/__init__.py
mkdir -p app/services/identity app/services/storage app/services/mailer app/templates
touch app/services/identity/__init__.py app/services/storage/__init__.py app/services/mailer/__init__.py
```

- [ ] **Step 9: Write `apps/api/Dockerfile` (DEV image — not a deploy artifact)**

```dockerfile
# Local development image. NOT a Cloud Run / production artifact.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /srv/app

COPY pyproject.toml ./
RUN uv sync --no-install-project --group dev

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 10: Write `docker-compose.yml`**

```yaml
name: unigate

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: unigate
      POSTGRES_PASSWORD: unigate
      POSTGRES_DB: unigate
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U unigate"]
      interval: 3s
      timeout: 3s
      retries: 20

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "no"]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      timeout: 3s
      retries: 20

  # Local stand-in for Workspace SMTP. Web UI at http://localhost:8025
  mailpit:
    image: axllent/mailpit:latest
    ports: ["8025:8025", "1025:1025"]

  # Local stand-in for GCS. Same JSON API, no credentials.
  fake-gcs:
    image: fsouza/fake-gcs-server:latest
    command: ["-scheme", "http", "-port", "4443", "-public-host", "localhost:4443"]
    ports: ["4443:4443"]
    volumes:
      - gcsdata:/data

  api:
    build: ./apps/api
    env_file: [.env]
    environment:
      DATABASE_URL: postgresql+asyncpg://unigate:unigate@postgres:5432/unigate
      REDIS_URL: redis://redis:6379/0
    ports: ["8000:8000"]
    volumes:
      - ./apps/api:/srv/app
      - ./scripts:/srv/scripts
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}

volumes:
  pgdata:
  gcsdata:
```

- [ ] **Step 11: Write `.env.example`, `Makefile`, `.gitignore`, `.dockerignore`**

`.env.example`:

```env
# ── app ──────────────────────────────────────────────────────────────────
APP_ENV=dev
APP_BASE_URL=http://localhost:8000
LOG_LEVEL=INFO
# generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=change-me-dev-only-not-a-real-secret

# ── the one event ────────────────────────────────────────────────────────
EVENT_SLUG=dryrun
EVENT_NAME=Collab Night
EVENT_VENUE=Main Auditorium
EVENT_STARTS_AT=2026-11-14T18:00:00+05:30
EVENT_ENDS_AT=2026-11-14T23:30:00+05:30
EVENT_SEATS_TOTAL=150

# ── datastores (compose overrides host names) ────────────────────────────
DATABASE_URL=postgresql+asyncpg://unigate:unigate@postgres:5432/unigate
REDIS_URL=redis://redis:6379/0

# ── identity ─────────────────────────────────────────────────────────────
# dev  = impersonate any seeded NetID, no Azure needed. REFUSED when APP_ENV=prod.
# entra = real Microsoft Entra OIDC.
AUTH_PROVIDER=dev
ALLOWED_DOMAINS=["university.edu"]
COOKIE_SECURE=false
ENTRA_TENANT_ID=
ENTRA_CLIENT_ID=
ENTRA_CLIENT_SECRET=

# ── object storage ───────────────────────────────────────────────────────
STORAGE_BACKEND=local
STORAGE_BUCKET=unigate-local
STORAGE_ENDPOINT_URL=http://fake-gcs:4443
STORAGE_PUBLIC_URL=http://localhost:4443

# ── email ────────────────────────────────────────────────────────────────
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_USE_TLS=false
SMTP_FROM=unigate@localhost

# ── staff seed: NetIDs granted roles by scripts/seed.py ───────────────────
SEED_OWNER_NET_IDS=["owner.test"]
SEED_ADMIN_NET_IDS=["admin.test"]
SEED_SECURITY_NET_IDS=["security.test"]
SEED_DEV_VISITOR_NET_IDS=["visitor.test"]
```

`Makefile`:

```makefile
.PHONY: up down logs build migrate revision seed test lint fmt shell psql reset

up:        ; docker compose up -d --build && docker compose logs -f api
down:      ; docker compose down
logs:      ; docker compose logs -f api
build:     ; docker compose build
migrate:   ; docker compose exec api alembic upgrade head
revision:  ; docker compose exec api alembic revision --autogenerate -m "$(m)"
seed:      ; docker compose exec api python /srv/scripts/seed.py
test:      ; docker compose run --rm api pytest -v
lint:      ; docker compose run --rm api ruff check .
fmt:       ; docker compose run --rm api ruff format .
shell:     ; docker compose exec api bash
psql:      ; docker compose exec postgres psql -U unigate -d unigate
reset:     ; docker compose down -v && docker compose up -d --build
```

`.gitignore`:

```gitignore
.env
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
.DS_Store
```

`.dockerignore`:

```dockerignore
.git
.venv
__pycache__
.pytest_cache
.ruff_cache
docs
*.md
.env
```

- [ ] **Step 12: Bring the stack up and run the tests**

```bash
cp .env.example .env
docker compose up -d --build
docker compose run --rm api pytest tests/ -v
```

Expected: 8 passed. If `test_health.py` errors on `ASGITransport`, confirm `httpx>=0.28` resolved.

- [ ] **Step 13: Verify the stack by hand**

```bash
curl -s localhost:8000/healthz          # {"status":"ok"}
open http://localhost:8025              # mailpit UI
curl -s localhost:4443/storage/v1/b     # fake-gcs responds
docker compose exec api ruff check .    # clean
```

- [ ] **Step 14: Commit**

```bash
git init 2>/dev/null; true
git add -A
git commit -m "feat(m1): compose stack, settings, PII-safe logging, error envelope, /healthz"
```

---

## Task 2: Database layer, models, Alembic baseline, `/readyz`

**Deliverable:** `alembic upgrade head` runs clean from an empty database and creates 7 tables; the `no_oversell` CHECK physically refuses an oversell; `/readyz` reports DB and Redis health.

**Files:**
- Create: `apps/api/app/db/base.py`, `apps/api/app/db/session.py`
- Create: `apps/api/app/models/__init__.py`, `apps/api/app/models/event.py`, `apps/api/app/models/user.py`, `apps/api/app/models/audit.py`
- Create: `apps/api/alembic.ini`, `apps/api/alembic/env.py`, `apps/api/alembic/script.py.mako`, `apps/api/alembic/versions/0001_core_tables.py`
- Create: `apps/api/app/core/deps.py`
- Modify: `apps/api/app/api/v1/routes/health.py` (add `/readyz`), `apps/api/app/main.py` (engine + redis in lifespan)
- Modify: `apps/api/tests/conftest.py` (db + redis fixtures)
- Test: `apps/api/tests/test_migrations.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 1.
- Produces:
  - `app.db.base.Base`, `UUIDPk`, `Timestamps`, `EventScoped`
  - `app.db.session.engine`, `session_factory`, `get_db() -> AsyncIterator[AsyncSession]`
  - `app.models.event.Event`, `Gate`, `EventInventory`, `GateType`
  - `app.models.user.User`, `EventStaff`, `StaffRole`
  - `app.models.audit.AuditLog`, `FunnelEvent`
  - `app.core.deps.get_db`, `get_redis`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_migrations.py`:

```python
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "events", "gates", "event_inventory",
    "users", "event_staff",
    "audit_log", "funnel_events",
}


async def test_all_core_tables_exist(db_session):
    rows = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = {row[0] for row in rows}
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"


async def test_staff_role_enum_has_exactly_the_v2_values(db_session):
    rows = await db_session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'staff_role'"
        )
    )
    assert {row[0] for row in rows} == {"OWNER", "ADMIN", "SECURITY"}


async def test_no_oversell_check_physically_refuses_an_oversell(db_session, event_id):
    """CLAUDE.md invariant: the database refuses to oversell even if app logic is wrong."""
    with pytest.raises(IntegrityError, match="no_oversell"):
        await db_session.execute(
            text(
                "UPDATE event_inventory SET seats_committed = seats_total + 1 "
                "WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        )
        await db_session.flush()


async def test_seats_committed_cannot_go_negative(db_session, event_id):
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE event_inventory SET seats_committed = -1 WHERE event_id = :event_id"),
            {"event_id": event_id},
        )
        await db_session.flush()


async def test_event_slug_is_case_insensitive(db_session):
    rows = await db_session.execute(
        text("SELECT count(*) FROM events WHERE slug = 'DRYRUN'")
    )
    assert rows.scalar() == 1


async def test_readyz_reports_both_dependencies(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}
```

- [ ] **Step 2: Extend `apps/api/tests/conftest.py`**

Append to the existing file:

```python
import asyncio
from uuid import UUID

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Creates the test database, runs every migration, yields an engine."""
    admin_url = "postgresql+asyncpg://unigate:unigate@postgres:5432/postgres"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS unigate_test WITH (FORCE)"))
        await conn.execute(text("CREATE DATABASE unigate_test OWNER unigate"))
    await admin.dispose()

    process = await asyncio.create_subprocess_exec(
        "alembic", "upgrade", "head",
        env={**os.environ, "DATABASE_URL": TEST_ENV["DATABASE_URL"]},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    assert process.returncode == 0, stdout.decode()

    engine = create_async_engine(TEST_ENV["DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """Function-scoped session on an outer transaction that is always rolled back."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest_asyncio.fixture
async def event_id(db_session) -> UUID:
    """The single seeded event, created fresh inside the test transaction."""
    from uuid_utils.compat import uuid7

    new_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO events (id, slug, name, venue, starts_at, ends_at, status, config) "
            "VALUES (:id, 'dryrun', 'Collab Night', 'Main Auditorium', "
            "now() + interval '30 days', now() + interval '30 days 5 hours', 'LIVE', :config)"
        ),
        {"id": new_id, "config": "{}"},
    )
    await db_session.execute(
        text(
            "INSERT INTO event_inventory (event_id, seats_total, seats_committed) "
            "VALUES (:id, 150, 0)"
        ),
        {"id": new_id},
    )
    await db_session.flush()
    return new_id


@pytest_asyncio.fixture
async def client(base_env, db_session):
    """ASGI client with the app's get_db overridden onto the test transaction."""
    from app.core.deps import get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()
```

Add `import os` to the top of `conftest.py` if it is not already there.

- [ ] **Step 3: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_migrations.py -v`
Expected: FAIL — `alembic: command not found` or `No such file or directory: 'alembic.ini'`.

- [ ] **Step 4: Write `apps/api/app/db/base.py` and `apps/api/app/db/session.py`**

`app/db/base.py`:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7


class Base(DeclarativeBase):
    pass


class UUIDPk:
    """UUIDv7 primary key, generated app-side (time-sortable, no PG extension)."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EventScoped:
    """CLAUDE.md: event_id on every table. Keeps the multi-event seam alive."""

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
```

`app/db/session.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    echo=_settings.db_echo,
)

session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
```

- [ ] **Step 5: Write the models**

`app/models/event.py`:

```python
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, Timestamps, UUIDPk


class GateType(StrEnum):
    ENTRY_ONLY = "ENTRY_ONLY"
    EXIT_ONLY = "EXIT_ONLY"
    BOTH = "BOTH"


gate_type_enum = ENUM(GateType, name="gate_type", create_type=False)


class Event(UUIDPk, Timestamps, Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    slug: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Kolkata")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registration_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    # Shape is addendum §A1: capacity, ticket_types, hold_ttl_minutes, hold_grace_minutes.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Gate(UUIDPk, EventScoped, Base):
    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("event_id", "name", name="gates_event_name_key"),)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[GateType] = mapped_column(gate_type_enum, nullable=False, server_default="BOTH")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class EventInventory(Base):
    """Addendum §A1: seats, not orders. ONE row per event, mutated under FOR UPDATE.

    `no_oversell` is the last line of defence — it holds even when application
    logic is wrong. Nothing may write these columns outside the inventory service.
    """

    __tablename__ = "event_inventory"
    __table_args__ = (
        CheckConstraint("seats_total > 0", name="seats_total_positive"),
        CheckConstraint("seats_committed >= 0", name="seats_committed_non_negative"),
        CheckConstraint("seats_committed <= seats_total", name="no_oversell"),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    seats_total: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_committed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`app/models/user.py`:

```python
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, ENUM
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, UUIDPk


class StaffRole(StrEnum):
    """Phase 0 decision 2.1 — replaces the five-value v1 enum.

    PARTICIPANT and VISITOR are derived at request time and never stored.
    """

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"


staff_role_enum = ENUM(StaffRole, name="staff_role", create_type=False)


class User(UUIDPk, Base):
    """Anyone who ever authenticated. `ms_object_id` is the real identity key."""

    __tablename__ = "users"

    ms_object_id: Mapped[str | None] = mapped_column(Text, unique=True)
    net_id: Mapped[str | None] = mapped_column(CITEXT, index=True)
    email: Mapped[str | None] = mapped_column(CITEXT)
    display_name: Mapped[str | None] = mapped_column(Text)
    auth_method: Mapped[str] = mapped_column(Text, nullable=False, server_default="MICROSOFT_SSO")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class EventStaff(UUIDPk, EventScoped, Base):
    """Seeded by hand (5-15 rows). Pre-provisioned by net_id before first login."""

    __tablename__ = "event_staff"
    __table_args__ = (
        UniqueConstraint("event_id", "net_id", "role", name="event_staff_event_netid_role_key"),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    net_id: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[StaffRole] = mapped_column(staff_role_enum, nullable=False)
    gate_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    added_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
```

`app/models/audit.py`:

```python
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import staff_role_enum


class AuditLog(Base):
    """Append only. Every payment approval, ID decision, override and export."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_net_id: Mapped[str | None] = mapped_column(CITEXT)
    actor_role: Mapped[str | None] = mapped_column(staff_role_enum)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_hash: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class FunnelEvent(Base):
    """Append only. Which step each person abandoned at — unbackfillable, so it
    exists from M1 even though M1 writes only LOGIN."""

    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    step: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
```

`app/models/__init__.py`:

```python
"""Import every model so Base.metadata is complete for Alembic autogenerate."""

from app.models.audit import AuditLog, FunnelEvent
from app.models.event import Event, EventInventory, Gate, GateType
from app.models.user import EventStaff, StaffRole, User

__all__ = [
    "AuditLog", "Event", "EventInventory", "EventStaff",
    "FunnelEvent", "Gate", "GateType", "StaffRole", "User",
]
```

- [ ] **Step 6: Write the Alembic scaffolding**

`apps/api/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
file_template = %%(rev)s_%%(slug)s
timezone = UTC

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`apps/api/alembic/env.py`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  — populates Base.metadata
from app.core.settings import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

`apps/api/alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 7: Write `apps/api/alembic/versions/0001_core_tables.py`**

```python
"""M1 core tables: events, gates, inventory, users, staff, audit, funnel.

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAFF_ROLE = postgresql.ENUM("OWNER", "ADMIN", "SECURITY", name="staff_role")
GATE_TYPE = postgresql.ENUM("ENTRY_ONLY", "EXIT_ONLY", "BOTH", name="gate_type")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    bind = op.get_bind()
    STAFF_ROLE.create(bind, checkfirst=True)
    GATE_TYPE.create(bind, checkfirst=True)

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("venue", sa.Text()),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True)),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("ends_at > starts_at", name="ends_after_start"),
    )

    op.create_table(
        "gates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", GATE_TYPE, nullable=False, server_default="BOTH"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("event_id", "name", name="gates_event_name_key"),
    )
    op.create_index("ix_gates_event_id", "gates", ["event_id"])

    # Addendum §A1. ONE row per event. `no_oversell` is the last line of defence.
    op.create_table(
        "event_inventory",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seats_total", sa.Integer(), nullable=False),
        sa.Column("seats_committed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("seats_total > 0", name="seats_total_positive"),
        sa.CheckConstraint("seats_committed >= 0", name="seats_committed_non_negative"),
        sa.CheckConstraint("seats_committed <= seats_total", name="no_oversell"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ms_object_id", sa.Text(), unique=True),
        sa.Column("net_id", postgresql.CITEXT()),
        sa.Column("email", postgresql.CITEXT()),
        sa.Column("display_name", sa.Text()),
        sa.Column("auth_method", sa.Text(), nullable=False, server_default="MICROSOFT_SSO"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_net_id", "users", ["net_id"])

    op.create_table(
        "event_staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("net_id", postgresql.CITEXT(), nullable=False),
        sa.Column("role", STAFF_ROLE, nullable=False),
        sa.Column("gate_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", "net_id", "role", name="event_staff_event_netid_role_key"),
    )
    op.create_index("ix_event_staff_event_id", "event_staff", ["event_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("actor_net_id", postgresql.CITEXT()),
        sa.Column("actor_role", STAFF_ROLE),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text()),
        sa.Column("object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("ip_hash", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_event_created", "audit_log", ["event_id", sa.text("created_at DESC")])
    op.create_index("ix_audit_log_object", "audit_log", ["object_type", "object_id"])

    op.create_table(
        "funnel_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_funnel_events_event_step", "funnel_events", ["event_id", "step", "created_at"])


def downgrade() -> None:
    op.drop_table("funnel_events")
    op.drop_table("audit_log")
    op.drop_table("event_staff")
    op.drop_table("users")
    op.drop_table("event_inventory")
    op.drop_table("gates")
    op.drop_table("events")
    bind = op.get_bind()
    GATE_TYPE.drop(bind, checkfirst=True)
    STAFF_ROLE.drop(bind, checkfirst=True)
```

- [ ] **Step 8: Write `apps/api/app/core/deps.py`**

```python
from collections.abc import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import session_factory

_redis: Redis | None = None


def get_redis_client() -> Redis:
    """Process-wide pooled Redis client."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def get_redis() -> Redis:
    return get_redis_client()
```

- [ ] **Step 9: Add `/readyz` to `apps/api/app/api/v1/routes/health.py`**

Replace the file with:

```python
import structlog
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness. Must not touch any dependency."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    """Readiness. Checks the two things the app cannot serve without."""
    database = "ok"
    cache = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        log.exception("readyz_database_failed")
        database = "error"
    try:
        await redis.ping()
    except Exception:
        log.exception("readyz_redis_failed")
        cache = "error"
    status = "ok" if database == "ok" and cache == "ok" else "degraded"
    return {"status": status, "database": database, "redis": cache}
```

- [ ] **Step 10: Run the migration and the tests**

```bash
docker compose exec api alembic upgrade head
docker compose run --rm api pytest tests/ -v
```

Expected: `alembic upgrade head` prints `Running upgrade  -> 0001` and exits 0. All tests pass.

- [ ] **Step 11: Verify the invariant by hand**

```bash
make psql
```

```sql
INSERT INTO events (id, slug, name, starts_at, ends_at)
  VALUES (gen_random_uuid(), 'checktest', 'x', now(), now() + interval '1 hour');
INSERT INTO event_inventory (event_id, seats_total, seats_committed)
  SELECT id, 150, 151 FROM events WHERE slug = 'checktest';
-- ERROR:  new row for relation "event_inventory" violates check constraint "no_oversell"
DELETE FROM events WHERE slug = 'checktest';
```

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat(m1): async db layer, core models, alembic baseline, /readyz"
```

---

## Task 3: Seed script

**Deliverable:** `make seed` creates the single event, its inventory row, one gate and the staff NetIDs from `.env`. Running it twice changes nothing.

**Files:**
- Create: `scripts/seed.py`
- Test: `apps/api/tests/test_seed.py`

**Interfaces:**
- Consumes: `app.models.*` (Task 2), `app.db.session.session_factory`.
- Produces: `scripts.seed.SeedConfig`, `async def seed(session: AsyncSession, config: SeedConfig) -> dict[str, int]` returning `{"events": n, "gates": n, "staff": n}` counts of rows *created* (0 on a re-run).

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_seed.py`:

```python
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

sys.path.insert(0, "/srv/scripts")


@pytest.fixture
def seed_config():
    from seed import SeedConfig

    now = datetime.now(timezone.utc)
    return SeedConfig(
        event_slug="dryrun",
        event_name="Collab Night",
        event_venue="Main Auditorium",
        event_starts_at=now + timedelta(days=30),
        event_ends_at=now + timedelta(days=30, hours=5),
        event_seats_total=150,
        seed_owner_net_ids=["owner.test"],
        seed_admin_net_ids=["admin.test"],
        seed_security_net_ids=["security.test"],
    )


async def test_seed_creates_event_inventory_and_staff(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventInventory, EventStaff

    created = await seed(db_session, seed_config)
    assert created["events"] == 1

    event = (await db_session.execute(select(Event).where(Event.slug == "dryrun"))).scalar_one()
    assert event.config["capacity"]["total_seats"] == 150
    assert {t["code"] for t in event.config["ticket_types"]} == {"SINGLE", "COUPLE"}
    assert event.config["hold_ttl_minutes"] == 20
    assert "hard_cap" not in event.config["capacity"]  # Phase 0 decision 2.3

    inventory = (
        await db_session.execute(select(EventInventory).where(EventInventory.event_id == event.id))
    ).scalar_one()
    assert (inventory.seats_total, inventory.seats_committed) == (150, 0)

    staff_count = await db_session.scalar(select(func.count()).select_from(EventStaff))
    assert staff_count == 3


async def test_seed_is_idempotent(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventStaff

    await seed(db_session, seed_config)
    second = await seed(db_session, seed_config)

    assert second == {"events": 0, "gates": 0, "staff": 0}
    assert await db_session.scalar(select(func.count()).select_from(Event)) == 1
    assert await db_session.scalar(select(func.count()).select_from(EventStaff)) == 3


async def test_reseed_does_not_clobber_committed_seats(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventInventory

    await seed(db_session, seed_config)
    event = (await db_session.execute(select(Event).where(Event.slug == "dryrun"))).scalar_one()
    inventory = (
        await db_session.execute(select(EventInventory).where(EventInventory.event_id == event.id))
    ).scalar_one()
    inventory.seats_committed = 42
    await db_session.flush()

    seed_config.event_seats_total = 170
    await seed(db_session, seed_config)
    await db_session.refresh(inventory)

    assert inventory.seats_total == 170     # capacity change applied
    assert inventory.seats_committed == 42  # live holds untouched
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed'`.

- [ ] **Step 3: Write `scripts/seed.py`**

```python
#!/usr/bin/env python
"""Seed the single event, its inventory, one gate and the staff NetIDs.

Idempotent: safe to run on every deploy. Never touches `seats_committed`.
"""
import asyncio
import sys
from datetime import datetime

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_factory
from app.models import Event, EventInventory, EventStaff, Gate, StaffRole


class SeedConfig(BaseSettings):
    """Pydantic at every boundary — including a script's boundary."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    event_slug: str = "dryrun"
    event_name: str = "Collab Night"
    event_venue: str = "Main Auditorium"
    event_timezone: str = "Asia/Kolkata"
    event_starts_at: datetime
    event_ends_at: datetime
    event_seats_total: int = 150
    event_gate_name: str = "Main Gate"

    seed_owner_net_ids: list[str] = Field(default_factory=list)
    seed_admin_net_ids: list[str] = Field(default_factory=list)
    seed_security_net_ids: list[str] = Field(default_factory=list)

    def event_config(self) -> dict:
        """Addendum §A1 shape. No `phases[]`, no `hard_cap` (Phase 0 decisions 2.2, 2.3)."""
        return {
            "capacity": {"total_seats": self.event_seats_total},
            "ticket_types": [
                {"code": "SINGLE", "label": "Single", "seats": 1, "price": 499, "active": True},
                {"code": "COUPLE", "label": "Couple", "seats": 2, "price": 899, "active": True},
            ],
            "hold_ttl_minutes": 20,
            "hold_grace_minutes": 10,
            "couple_partner_mode": "SELF_CLAIM",
        }

    def staff_grants(self) -> list[tuple[str, StaffRole]]:
        return [
            *[(n, StaffRole.OWNER) for n in self.seed_owner_net_ids],
            *[(n, StaffRole.ADMIN) for n in self.seed_admin_net_ids],
            *[(n, StaffRole.SECURITY) for n in self.seed_security_net_ids],
        ]


async def seed(session: AsyncSession, config: SeedConfig) -> dict[str, int]:
    created = {"events": 0, "gates": 0, "staff": 0}

    event = await session.scalar(select(Event).where(Event.slug == config.event_slug))
    if event is None:
        event = Event(
            slug=config.event_slug,
            name=config.event_name,
            venue=config.event_venue,
            timezone=config.event_timezone,
            starts_at=config.event_starts_at,
            ends_at=config.event_ends_at,
            status="LIVE",
            config=config.event_config(),
        )
        session.add(event)
        await session.flush()
        created["events"] = 1
    else:
        event.name = config.event_name
        event.venue = config.event_venue
        event.starts_at = config.event_starts_at
        event.ends_at = config.event_ends_at
        event.config = config.event_config()

    inventory = await session.get(EventInventory, event.id)
    if inventory is None:
        session.add(EventInventory(event_id=event.id, seats_total=config.event_seats_total))
    else:
        # Capacity may be adjusted; committed seats are live state and are never reset.
        inventory.seats_total = config.event_seats_total

    gate = await session.scalar(
        select(Gate).where(Gate.event_id == event.id, Gate.name == config.event_gate_name)
    )
    if gate is None:
        session.add(Gate(event_id=event.id, name=config.event_gate_name))
        created["gates"] = 1

    for net_id, role in config.staff_grants():
        existing = await session.scalar(
            select(EventStaff).where(
                EventStaff.event_id == event.id,
                EventStaff.net_id == net_id,
                EventStaff.role == role,
            )
        )
        if existing is None:
            session.add(EventStaff(event_id=event.id, net_id=net_id, role=role))
            created["staff"] += 1
        else:
            existing.is_active = True

    await session.flush()
    return created


async def main() -> None:
    config = SeedConfig()
    async with session_factory() as session:
        created = await seed(session, config)
        await session.commit()
    print(f"seed complete: {created}")  # noqa: T201


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm api pytest tests/test_seed.py -v`
Expected: 3 passed.

- [ ] **Step 5: Seed the real database and verify idempotency by hand**

```bash
make seed        # seed complete: {'events': 1, 'gates': 1, 'staff': 3}
make seed        # seed complete: {'events': 0, 'gates': 0, 'staff': 0}
docker compose exec postgres psql -U unigate -d unigate \
  -c "SELECT net_id, role FROM event_staff ORDER BY role;"
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(m1): idempotent seed script for event, inventory, gate and staff"
```

---

## Task 4: Redis-backed opaque sessions + CSRF

**Deliverable:** a session service that creates, loads, slides, and revokes opaque sessions, with a per-user index so "kill every session for this person" is one call instead of a keyspace scan. Double-submit CSRF on every state-changing request.

**Files:**
- Create: `apps/api/app/core/security.py`, `apps/api/app/services/identity/sessions.py`
- Modify: `apps/api/app/core/deps.py` (session + CSRF dependencies)
- Test: `apps/api/tests/test_sessions.py`

**Interfaces:**
- Consumes: `get_settings()`, `get_redis()`.
- Produces:
  - `app.core.security.new_token(nbytes: int = 32) -> str`, `constant_time_equals(a: str, b: str) -> bool`, `hash_value(v: str) -> str`
  - `app.services.identity.sessions.SessionData` (Pydantic) with fields `sid, user_id, net_id, display_name, email, roles, csrf_token, is_staff, created_at, ip_hash, ua_hash`
  - `create_session(redis, *, user_id, net_id, display_name, email, roles, is_staff, ip_hash=None, ua_hash=None) -> SessionData`
  - `load_session(redis, sid) -> SessionData | None`
  - `destroy_session(redis, sid) -> None`
  - `destroy_user_sessions(redis, user_id) -> int`
  - `app.core.deps.current_session(request, redis) -> SessionData` (raises `AppError(UNAUTHENTICATED, http_status=401)`)
  - `app.core.deps.optional_session(request, redis) -> SessionData | None`
  - `app.core.deps.require_csrf(request, session) -> None`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_sessions.py`:

```python
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis


@pytest_asyncio.fixture
async def redis(settings):
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_create_then_load_round_trips(redis):
    from app.services.identity.sessions import create_session, load_session

    user_id = uuid4()
    created = await create_session(
        redis, user_id=user_id, net_id="owner.test", display_name="Owner Test",
        email="owner@university.edu", roles=["OWNER"], is_staff=True,
    )
    loaded = await load_session(redis, created.sid)

    assert loaded is not None
    assert loaded.user_id == user_id
    assert loaded.roles == ["OWNER"]
    assert loaded.csrf_token == created.csrf_token


async def test_unknown_sid_loads_as_none(redis):
    from app.services.identity.sessions import load_session

    assert await load_session(redis, "not-a-real-sid") is None


async def test_staff_ttl_is_fixed_and_participant_ttl_slides(redis, settings):
    from app.services.identity.sessions import create_session, load_session

    staff = await create_session(
        redis, user_id=uuid4(), net_id="admin.test", display_name="A",
        email=None, roles=["ADMIN"], is_staff=True,
    )
    visitor = await create_session(
        redis, user_id=uuid4(), net_id="visitor.test", display_name="V",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    assert await redis.ttl(f"sess:{staff.sid}") == pytest.approx(
        settings.session_ttl_staff_seconds, abs=5
    )

    await redis.expire(f"sess:{visitor.sid}", 60)
    await load_session(redis, visitor.sid)  # sliding refresh
    assert await redis.ttl(f"sess:{visitor.sid}") == pytest.approx(
        settings.session_ttl_participant_seconds, abs=5
    )

    await redis.expire(f"sess:{staff.sid}", 60)
    await load_session(redis, staff.sid)    # fixed — must NOT extend
    assert await redis.ttl(f"sess:{staff.sid}") <= 60


async def test_destroy_session_removes_it(redis):
    from app.services.identity.sessions import create_session, destroy_session, load_session

    session = await create_session(
        redis, user_id=uuid4(), net_id="x", display_name="X",
        email=None, roles=["VISITOR"], is_staff=False,
    )
    await destroy_session(redis, session.sid)
    assert await load_session(redis, session.sid) is None


async def test_destroy_user_sessions_kills_every_device(redis):
    """LLD-v2 §0: 'you see a device list and can kill any session'."""
    from app.services.identity.sessions import (
        create_session, destroy_user_sessions, load_session,
    )

    user_id = uuid4()
    sessions = [
        await create_session(
            redis, user_id=user_id, net_id="sec.test", display_name="S",
            email=None, roles=["SECURITY"], is_staff=True,
        )
        for _ in range(3)
    ]
    other = await create_session(
        redis, user_id=uuid4(), net_id="other", display_name="O",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    killed = await destroy_user_sessions(redis, user_id)

    assert killed == 3
    for session in sessions:
        assert await load_session(redis, session.sid) is None
    assert await load_session(redis, other.sid) is not None


async def test_user_index_is_pruned_on_single_destroy(redis):
    from app.services.identity.sessions import create_session, destroy_session

    user_id = uuid4()
    session = await create_session(
        redis, user_id=user_id, net_id="x", display_name="X",
        email=None, roles=["VISITOR"], is_staff=False,
    )
    await destroy_session(redis, session.sid)
    assert await redis.smembers(f"sess:user:{user_id}") == set()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.identity.sessions'`.

- [ ] **Step 3: Write `apps/api/app/core/security.py`**

```python
import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    """Opaque, unguessable token. Used for session ids, CSRF tokens, OIDC state."""
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def hash_value(value: str) -> str:
    """Non-reversible fingerprint for storing IP/UA without storing PII."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def client_ip_prefix(ip: str) -> str:
    """Soft-bind to a /24 — campus wifi roams, so this must not be a hard block."""
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ip
```

- [ ] **Step 4: Write `apps/api/app/services/identity/sessions.py`**

```python
"""Opaque, server-side sessions in Redis.

Opaque and not JWT because revocation must be instant — a stolen scanner phone
or a staff member removed mid-event (LLD-v1 §8.2).
"""
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.security import new_token
from app.core.settings import get_settings

SESSION_PREFIX = "sess:"
USER_INDEX_PREFIX = "sess:user:"


class SessionData(BaseModel):
    sid: str
    user_id: UUID
    net_id: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    csrf_token: str
    is_staff: bool
    created_at: datetime
    ip_hash: str | None = None
    ua_hash: str | None = None


def _ttl_for(is_staff: bool) -> int:
    settings = get_settings()
    return (
        settings.session_ttl_staff_seconds
        if is_staff
        else settings.session_ttl_participant_seconds
    )


async def create_session(
    redis: Redis,
    *,
    user_id: UUID,
    net_id: str,
    display_name: str,
    email: str | None,
    roles: list[str],
    is_staff: bool,
    ip_hash: str | None = None,
    ua_hash: str | None = None,
) -> SessionData:
    session = SessionData(
        sid=new_token(32),
        user_id=user_id,
        net_id=net_id,
        display_name=display_name,
        email=email,
        roles=roles,
        csrf_token=new_token(24),
        is_staff=is_staff,
        created_at=datetime.now(timezone.utc),
        ip_hash=ip_hash,
        ua_hash=ua_hash,
    )
    ttl = _ttl_for(is_staff)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(f"{SESSION_PREFIX}{session.sid}", session.model_dump_json(), ex=ttl)
        pipe.sadd(f"{USER_INDEX_PREFIX}{user_id}", session.sid)
        # Index outlives the longest possible session so it never orphans a live sid.
        pipe.expire(f"{USER_INDEX_PREFIX}{user_id}", ttl + 3600)
        await pipe.execute()
    return session


async def load_session(redis: Redis, sid: str) -> SessionData | None:
    raw = await redis.get(f"{SESSION_PREFIX}{sid}")
    if raw is None:
        return None
    session = SessionData.model_validate_json(raw)
    # Participants slide (8h of activity); staff sessions are fixed to a shift (12h).
    if not session.is_staff:
        await redis.expire(f"{SESSION_PREFIX}{sid}", _ttl_for(False))
    return session


async def destroy_session(redis: Redis, sid: str) -> None:
    raw = await redis.get(f"{SESSION_PREFIX}{sid}")
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(f"{SESSION_PREFIX}{sid}")
        if raw is not None:
            user_id = SessionData.model_validate_json(raw).user_id
            pipe.srem(f"{USER_INDEX_PREFIX}{user_id}", sid)
        await pipe.execute()


async def destroy_user_sessions(redis: Redis, user_id: UUID) -> int:
    """Kill every live session for one identity. The device-list 'revoke' button."""
    index_key = f"{USER_INDEX_PREFIX}{user_id}"
    sids = await redis.smembers(index_key)
    if not sids:
        return 0
    async with redis.pipeline(transaction=True) as pipe:
        for sid in sids:
            pipe.delete(f"{SESSION_PREFIX}{sid}")
        pipe.delete(index_key)
        results = await pipe.execute()
    return sum(1 for deleted in results[:-1] if deleted)
```

- [ ] **Step 5: Add the session dependencies to `apps/api/app/core/deps.py`**

Append to the file:

```python
from fastapi import Depends, Request, status

from app.core.errors import AppError, ErrorCode
from app.core.security import constant_time_equals
from app.services.identity.sessions import SessionData, load_session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


async def optional_session(
    request: Request, redis: Redis = Depends(get_redis)
) -> SessionData | None:
    sid = request.cookies.get(get_settings().session_cookie_name)
    if not sid:
        return None
    return await load_session(redis, sid)


async def current_session(
    session: SessionData | None = Depends(optional_session),
) -> SessionData:
    if session is None:
        raise AppError(
            ErrorCode.UNAUTHENTICATED,
            "Sign in to continue.",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    return session


async def require_csrf(
    request: Request, session: SessionData = Depends(current_session)
) -> None:
    """Double-submit. SameSite=Lax alone is not enough for the scanner's POSTs."""
    if request.method in SAFE_METHODS:
        return
    presented = request.headers.get("x-csrf-token") or request.cookies.get(
        get_settings().csrf_cookie_name, ""
    )
    if not presented or not constant_time_equals(presented, session.csrf_token):
        raise AppError(
            ErrorCode.CSRF_INVALID,
            "Your session token did not match. Reload the page and try again.",
            http_status=status.HTTP_403_FORBIDDEN,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm api pytest tests/test_sessions.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(m1): opaque redis sessions with per-user revocation index and CSRF"
```

---

## Task 5: Auth provider seam, DevAuthProvider, login flow

**Deliverable:** visiting `/` shows a login page; picking a seeded NetID logs you in with the right roles; the dev picker returns 404 when `AUTH_PROVIDER=entra`.

> **UI note:** `login.html` and `base.html` are user-facing. Invoke the `frontend-design` skill before writing them — the markup below is functionally complete and is the starting point for that pass, not a substitute for it.

**Files:**
- Create: `apps/api/app/services/identity/provider.py`, `apps/api/app/services/identity/dev.py`, `apps/api/app/services/identity/users.py`, `apps/api/app/services/identity/roles.py`
- Create: `apps/api/app/api/v1/routes/auth.py`
- Create: `apps/api/app/templates/base.html`, `apps/api/app/templates/login.html`
- Modify: `apps/api/app/main.py` (mount auth router, Jinja templates)
- Test: `apps/api/tests/test_auth_dev.py`

**Interfaces:**
- Consumes: `SessionData`, `create_session`, `destroy_session` (Task 4); `User`, `EventStaff`, `StaffRole`, `Event` (Task 2).
- Produces:
  - `app.services.identity.provider.IdentityClaims` (Pydantic: `subject, net_id, email, display_name, auth_method`)
  - `app.services.identity.provider.AuthProvider` (Protocol: `name: str`, `async authorization_url(*, state, next_url) -> str`, `async exchange(*, state, params: Mapping[str, str], stored: dict) -> IdentityClaims`, `async state_payload() -> dict`)
  - `app.services.identity.provider.get_auth_provider() -> AuthProvider`
  - `app.services.identity.dev.DevAuthProvider`
  - `app.services.identity.users.upsert_user(db, claims) -> User`
  - `app.services.identity.roles.Role` (StrEnum: `OWNER, ADMIN, SECURITY, PARTICIPANT, VISITOR`), `resolve_roles(db, event_id, user) -> list[Role]`, `get_current_event(db) -> Event`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_auth_dev.py`:

```python
import pytest
from sqlalchemy import select, text


@pytest.fixture
async def seeded_staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, 'owner.test', 'OWNER'), "
            "(gen_random_uuid(), :e, 'admin.test', 'ADMIN'), "
            "(gen_random_uuid(), :e, 'security.test', 'SECURITY')"
        ),
        {"e": event_id},
    )
    await db_session.flush()


async def _dev_login(client, net_id: str):
    start = await client.get("/auth/start", params={"next": "/"}, follow_redirects=False)
    assert start.status_code == 307
    state = start.headers["location"].split("state=")[1].split("&")[0]
    return await client.post(
        "/auth/callback",
        data={"state": state, "net_id": net_id},
        follow_redirects=False,
    )


async def test_login_page_renders(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Sign in" in response.text


async def test_dev_login_as_owner_sets_a_session_with_owner_role(client, seeded_staff, settings):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    response = await _dev_login(client, "owner.test")

    assert response.status_code == 303
    sid = response.cookies[settings.session_cookie_name]
    session = await load_session(get_redis_client(), sid)
    assert session is not None
    assert session.roles == ["OWNER"]
    assert session.is_staff is True
    assert session.net_id == "owner.test"


@pytest.mark.parametrize(
    ("net_id", "expected"),
    [
        ("owner.test", ["OWNER"]),
        ("admin.test", ["ADMIN"]),
        ("security.test", ["SECURITY"]),
        ("random.student", ["VISITOR"]),
    ],
)
async def test_roles_resolve_per_net_id(client, seeded_staff, settings, net_id, expected):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    response = await _dev_login(client, net_id)
    session = await load_session(get_redis_client(), response.cookies[settings.session_cookie_name])
    assert session.roles == expected


async def test_unseeded_net_id_is_a_visitor_not_an_error(client, seeded_staff):
    response = await _dev_login(client, "random.student")
    assert response.status_code == 303


async def test_login_creates_exactly_one_user_row_across_two_logins(client, db_session, seeded_staff):
    from sqlalchemy import func

    from app.models import User

    await _dev_login(client, "owner.test")
    await _dev_login(client, "owner.test")

    count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.net_id == "owner.test")
    )
    assert count == 1


async def test_login_backfills_event_staff_user_id(client, db_session, seeded_staff):
    from app.models import EventStaff, User

    await _dev_login(client, "owner.test")

    user = (await db_session.execute(select(User).where(User.net_id == "owner.test"))).scalar_one()
    staff = (
        await db_session.execute(select(EventStaff).where(EventStaff.net_id == "owner.test"))
    ).scalar_one()
    assert staff.user_id == user.id


async def test_callback_with_an_unknown_state_is_rejected(client, seeded_staff):
    response = await client.post(
        "/auth/callback", data={"state": "forged", "net_id": "owner.test"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OIDC_STATE_INVALID"


async def test_state_is_single_use(client, seeded_staff):
    start = await client.get("/auth/start", params={"next": "/"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]

    first = await client.post("/auth/callback", data={"state": state, "net_id": "owner.test"}, follow_redirects=False)
    second = await client.post("/auth/callback", data={"state": state, "net_id": "owner.test"}, follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 400


async def test_dev_picker_is_404_when_provider_is_entra(monkeypatch, db_session):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_db
    from app.core.settings import get_settings
    from app.main import create_app

    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/auth/dev/picker", params={"state": "x"})
    assert response.status_code == 404


async def test_logout_destroys_the_session(client, seeded_staff, settings):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    login = await _dev_login(client, "owner.test")
    sid = login.cookies[settings.session_cookie_name]

    session = await load_session(get_redis_client(), sid)
    response = await client.post(
        "/auth/logout",
        headers={"x-csrf-token": session.csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert await load_session(get_redis_client(), sid) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_auth_dev.py -v`
Expected: FAIL — 404 on `/auth/start`, module import errors for `app.services.identity.provider`.

- [ ] **Step 3: Write `apps/api/app/services/identity/provider.py`**

```python
"""The auth seam.

Two implementations, chosen by `AUTH_PROVIDER`:
  dev   — impersonate any NetID. Local only; `Settings` refuses it when APP_ENV=prod.
  entra — real Microsoft Entra OIDC (Authorization Code + PKCE).

A third (email OTP for non-university attendees, LLD-v2 §15 item 5) drops in here
without touching a route.
"""
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.settings import AuthProviderName, get_settings


class IdentityClaims(BaseModel):
    """What every provider must produce. Nothing provider-specific leaks past this."""

    subject: str          # immutable identity key (Entra `oid`, or `dev:<net_id>`)
    net_id: str           # lowercased, trimmed — for display and matching
    email: str | None
    display_name: str
    auth_method: str


@runtime_checkable
class AuthProvider(Protocol):
    name: str

    async def state_payload(self) -> dict:
        """Provider-specific values to stash in Redis alongside the state (PKCE, nonce)."""
        ...

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        ...

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        ...


def get_auth_provider() -> AuthProvider:
    """Derived from the settings enum, not a hardcoded if/else chain."""
    from app.services.identity.dev import DevAuthProvider
    from app.services.identity.entra import EntraAuthProvider

    registry: dict[AuthProviderName, type[AuthProvider]] = {
        AuthProviderName.DEV: DevAuthProvider,
        AuthProviderName.ENTRA: EntraAuthProvider,
    }
    missing = set(AuthProviderName) - set(registry)
    if missing:
        raise RuntimeError(f"AuthProvider registry is missing implementations for {missing}")
    return registry[get_settings().auth_provider]()
```

- [ ] **Step 4: Write `apps/api/app/services/identity/dev.py`**

```python
"""Impersonate any NetID without an Azure app registration.

Never reachable in production: `Settings._enforce_environment_guards` raises at
import time if APP_ENV=prod and AUTH_PROVIDER=dev, and the picker route 404s
whenever the active provider is not this one.
"""
from collections.abc import Mapping

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.services.identity.provider import IdentityClaims


class DevAuthProvider:
    name = "dev"

    async def state_payload(self) -> dict:
        return {}

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        return f"/auth/dev/picker?state={state}"

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        net_id = (params.get("net_id") or "").strip().lower()
        if not net_id:
            raise AppError(ErrorCode.UNKNOWN_NET_ID, "Pick a NetID to sign in as.")
        domains = get_settings().allowed_domains
        domain = domains[0] if domains else "localhost"
        return IdentityClaims(
            subject=f"dev:{net_id}",
            net_id=net_id,
            email=f"{net_id}@{domain}",
            display_name=" ".join(part.capitalize() for part in net_id.replace(".", " ").split()),
            auth_method="DEV",
        )
```

- [ ] **Step 5: Write `apps/api/app/services/identity/users.py` and `roles.py`**

`app/services/identity/users.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventStaff, User
from app.services.identity.provider import IdentityClaims


async def upsert_user(db: AsyncSession, claims: IdentityClaims) -> User:
    """`subject` is the identity key — `net_id` can change when a roll format does."""
    user = await db.scalar(select(User).where(User.ms_object_id == claims.subject))
    if user is None:
        user = User(ms_object_id=claims.subject, auth_method=claims.auth_method)
        db.add(user)
    user.net_id = claims.net_id
    user.email = claims.email
    user.display_name = claims.display_name
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    return user


async def bind_staff_rows(db: AsyncSession, event_id, user: User) -> None:
    """Staff are pre-provisioned by NetID; first login attaches the user row."""
    await db.execute(
        update(EventStaff)
        .where(
            EventStaff.event_id == event_id,
            EventStaff.net_id == user.net_id,
            EventStaff.user_id.is_(None),
        )
        .values(user_id=user.id)
    )
    await db.flush()
```

`app/services/identity/roles.py`:

```python
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.models import Event, EventStaff, StaffRole, User

# Staff roles are the source of truth; the derived roles extend it.
STAFF_ROLE_NAMES = frozenset(role.value for role in StaffRole)


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"
    PARTICIPANT = "PARTICIPANT"   # authenticated + holds an approved ticket (from M2)
    VISITOR = "VISITOR"           # authenticated, no ticket


# Fail loudly at import if the two enums ever drift apart.
assert STAFF_ROLE_NAMES <= {role.value for role in Role}


async def get_current_event(db: AsyncSession) -> Event:
    """One event. Resolved by slug from settings, never passed in by a client."""
    slug = get_settings().event_slug
    event = await db.scalar(select(Event).where(Event.slug == slug))
    if event is None:
        raise AppError(
            ErrorCode.EVENT_NOT_CONFIGURED,
            f"No event with slug '{slug}'. Run `make seed`.",
            http_status=500,
        )
    return event


async def resolve_roles(db: AsyncSession, event_id, user: User) -> list[Role]:
    """Staff roles come from `event_staff`. Everyone else is a VISITOR.

    PARTICIPANT is added at M2, when `tickets` exists and an approved ticket can
    be looked up. Until then a ticket cannot exist, so nobody can be one.
    """
    rows = await db.execute(
        select(EventStaff.role).where(
            EventStaff.event_id == event_id,
            EventStaff.net_id == user.net_id,
            EventStaff.is_active.is_(True),
        )
    )
    staff_roles = [Role(row[0].value) for row in rows]
    if staff_roles:
        order = list(Role)
        return sorted(set(staff_roles), key=order.index)
    return [Role.VISITOR]
```

- [ ] **Step 6: Write `apps/api/app/api/v1/routes/auth.py`**

```python
import json

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, get_redis, optional_session, require_csrf
from app.core.errors import AppError, ErrorCode
from app.core.security import client_ip_prefix, hash_value, new_token
from app.core.settings import AuthProviderName, get_settings
from app.services.identity.provider import get_auth_provider
from app.services.identity.roles import get_current_event, resolve_roles
from app.services.identity.sessions import (
    SessionData, create_session, destroy_session,
)
from app.services.identity.users import bind_staff_rows, upsert_user

router = APIRouter(tags=["auth"])
log = structlog.get_logger()

STATE_PREFIX = "oidc:state:"


def _set_auth_cookies(response: Response, session: SessionData) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name, session.sid,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    # Readable by JS on purpose — this is the "double" of double-submit CSRF.
    response.set_cookie(
        settings.csrf_cookie_name, session.csrf_token,
        httponly=False, secure=settings.cookie_secure, samesite="lax", path="/",
    )


@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request, session=Depends(optional_session)) -> Response:
    if session is not None:
        return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)
    settings = get_settings()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"is_dev_provider": settings.auth_provider is AuthProviderName.DEV},
    )


@router.get("/auth/start")
async def auth_start(
    next: str = "/me",
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    settings = get_settings()
    provider = get_auth_provider()
    state = new_token(24)
    stored = {"next": next if next.startswith("/") else "/me", **(await provider.state_payload())}
    await redis.set(
        f"{STATE_PREFIX}{state}", json.dumps(stored), ex=settings.oidc_state_ttl_seconds
    )
    url = await provider.authorization_url(state=state, next_url=stored["next"], stored=stored)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/auth/dev/picker", response_class=HTMLResponse)
async def dev_picker(request: Request, state: str, db: AsyncSession = Depends(get_db)) -> Response:
    """Only exists while the dev provider is active. 404 otherwise — not 403,
    because the route's existence is itself information."""
    from sqlalchemy import select

    from app.models import EventStaff

    if get_settings().auth_provider is not AuthProviderName.DEV:
        raise AppError(ErrorCode.NOT_FOUND, "Not found.", http_status=status.HTTP_404_NOT_FOUND)
    event = await get_current_event(db)
    rows = await db.execute(
        select(EventStaff.net_id, EventStaff.role).where(EventStaff.event_id == event.id)
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dev_picker.html",
        context={"state": state, "staff": [(r[0], r[1].value) for r in rows]},
    )


@router.api_route("/auth/callback", methods=["GET", "POST"])
async def auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """One callback for every provider. Entra arrives as GET with query params;
    the dev picker posts a form. Both land here."""
    params = dict(request.query_params)
    if request.method == "POST":
        params.update({k: str(v) for k, v in (await request.form()).items()})

    state = params.get("state", "")
    raw = await redis.getdel(f"{STATE_PREFIX}{state}") if state else None
    if raw is None:
        raise AppError(
            ErrorCode.OIDC_STATE_INVALID,
            "That sign-in link has expired. Start again.",
        )
    stored = json.loads(raw)

    claims = await get_auth_provider().exchange(params=params, stored=stored)

    settings = get_settings()
    if settings.allowed_domains and claims.email:
        domain = claims.email.rsplit("@", 1)[-1].lower()
        if domain not in {d.lower() for d in settings.allowed_domains}:
            raise AppError(
                ErrorCode.DOMAIN_NOT_ALLOWED,
                "Use your university account to sign in.",
                http_status=status.HTTP_403_FORBIDDEN,
            )

    event = await get_current_event(db)
    user = await upsert_user(db, claims)
    await bind_staff_rows(db, event.id, user)
    roles = await resolve_roles(db, event.id, user)
    await db.commit()

    client_host = request.client.host if request.client else ""
    session = await create_session(
        redis,
        user_id=user.id,
        net_id=user.net_id or claims.net_id,
        display_name=user.display_name or claims.display_name,
        email=user.email,
        roles=[role.value for role in roles],
        is_staff=any(role.value in {"OWNER", "ADMIN", "SECURITY"} for role in roles),
        ip_hash=hash_value(client_ip_prefix(client_host)) if client_host else None,
        ua_hash=hash_value(request.headers.get("user-agent", "")),
    )
    log.info("login", user_id=str(user.id), roles=[r.value for r in roles], provider=claims.auth_method)

    response = RedirectResponse(stored.get("next", "/me"), status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookies(response, session)
    return response


@router.post("/auth/logout", dependencies=[Depends(require_csrf)])
async def logout(
    session: SessionData = Depends(current_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    await destroy_session(redis, session.sid)
    settings = get_settings()
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response
```

- [ ] **Step 7: Write the templates**

`apps/api/app/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}unigate{% endblock %}</title>
  <style>
    :root { color-scheme: light dark; --ink:#12131a; --bg:#fbfbfd; --muted:#6b6f80;
            --line:#e3e4ea; --accent:#2f5fe0; }
    @media (prefers-color-scheme: dark) {
      :root { --ink:#eceef5; --bg:#101116; --muted:#9aa0b4; --line:#272935; --accent:#7fa0ff; }
    }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); min-height:100dvh;
           display:grid; place-items:center; padding:2rem;
           font:16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
    main { width:min(28rem, 100%); }
    h1 { font-size:1.5rem; letter-spacing:-0.02em; margin:0 0 .25rem; }
    p.sub { color:var(--muted); margin:0 0 2rem; }
    .card { border:1px solid var(--line); border-radius:12px; padding:1.5rem; background:transparent; }
    button, .btn { display:block; width:100%; padding:.75rem 1rem; border-radius:8px;
                   border:1px solid var(--line); background:transparent; color:inherit;
                   font:inherit; text-align:left; cursor:pointer; }
    button.primary { background:var(--accent); border-color:var(--accent); color:#fff;
                     text-align:center; font-weight:600; }
    .role { color:var(--muted); font-size:.85rem; }
    ul { list-style:none; margin:0; padding:0; display:grid; gap:.5rem; }
    .note { margin-top:1.5rem; font-size:.85rem; color:var(--muted); }
  </style>
</head>
<body>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`apps/api/app/templates/login.html`:

```html
{% extends "base.html" %}
{% block title %}Sign in · unigate{% endblock %}
{% block content %}
  <h1>Sign in</h1>
  <p class="sub">Use your university account to continue.</p>
  <div class="card">
    <form action="/auth/start" method="get">
      <input type="hidden" name="next" value="/me">
      <button class="primary" type="submit">Sign in with Microsoft</button>
    </form>
    {% if is_dev_provider %}
      <p class="note">Development mode — no Azure app registration required.
      The next screen lets you sign in as any seeded NetID.</p>
    {% endif %}
  </div>
{% endblock %}
```

`apps/api/app/templates/dev_picker.html`:

```html
{% extends "base.html" %}
{% block title %}Choose an identity · unigate{% endblock %}
{% block content %}
  <h1>Choose an identity</h1>
  <p class="sub">Development provider. Pick a seeded NetID, or type any other to
  sign in as a VISITOR.</p>
  <div class="card">
    <ul>
      {% for net_id, role in staff %}
      <li>
        <form action="/auth/callback" method="post">
          <input type="hidden" name="state" value="{{ state }}">
          <input type="hidden" name="net_id" value="{{ net_id }}">
          <button type="submit">{{ net_id }} <span class="role">— {{ role }}</span></button>
        </form>
      </li>
      {% endfor %}
      <li>
        <form action="/auth/callback" method="post">
          <input type="hidden" name="state" value="{{ state }}">
          <input name="net_id" placeholder="any.other.netid" required
                 style="width:100%;padding:.75rem 1rem;border-radius:8px;
                        border:1px solid var(--line);background:transparent;
                        color:inherit;font:inherit;margin-bottom:.5rem">
          <button type="submit">Sign in as a VISITOR</button>
        </form>
      </li>
    </ul>
  </div>
{% endblock %}
```

- [ ] **Step 8: Mount templates and the auth router in `apps/api/app/main.py`**

Inside `create_app()`, after `register_error_handlers(app)`:

```python
    from pathlib import Path

    from fastapi.templating import Jinja2Templates

    from app.api.v1.routes import auth

    app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.include_router(auth.router)
```

- [ ] **Step 9: Run the tests**

Run: `docker compose run --rm api pytest tests/test_auth_dev.py -v`
Expected: 11 passed.

- [ ] **Step 10: Verify by hand in a browser**

```bash
make seed
open http://localhost:8000/
```
Click "Sign in with Microsoft" → the dev picker lists `owner.test`, `admin.test`, `security.test` → pick one → redirected to `/me` (404 until Task 6, which is expected).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(m1): auth provider seam, dev provider, login flow, single-use state"
```

---

## Task 6: Role dependency and `GET /api/v1/me`

**Deliverable:** `/api/v1/me` returns the Phase 0 decision-2.4 payload; `require_role` denies by default; every login writes an audit row and a funnel row.

**Files:**
- Create: `apps/api/app/schemas/me.py`, `apps/api/app/api/v1/routes/me.py`, `apps/api/app/services/audit.py`
- Modify: `apps/api/app/core/deps.py` (add `require_role`), `apps/api/app/main.py` (mount me router), `apps/api/app/api/v1/routes/auth.py` (write audit + funnel rows on login)
- Test: `apps/api/tests/test_me.py`

**Interfaces:**
- Consumes: `current_session`, `Role`, `get_current_event`.
- Produces:
  - `app.schemas.me.EventSummary`, `app.schemas.me.MeResponse`
  - `app.core.deps.require_role(*roles: Role) -> Callable` (FastAPI dependency factory)
  - `app.services.audit.write_audit(db, *, event_id, actor, action, object_type=None, object_id=None, before=None, after=None, request=None) -> None`
  - `app.services.audit.write_funnel_event(db, *, event_id, user_id, step) -> None`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_me.py`:

```python
import pytest
from sqlalchemy import func, select, text


@pytest.fixture
async def seeded_staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, 'owner.test', 'OWNER'), "
            "(gen_random_uuid(), :e, 'admin.test', 'ADMIN')"
        ),
        {"e": event_id},
    )
    await db_session.flush()


async def _dev_login(client, net_id: str):
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    return await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )


async def test_me_requires_a_session(client):
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_me_returns_the_owner_payload(client, seeded_staff):
    await _dev_login(client, "owner.test")
    response = await client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["net_id"] == "owner.test"
    assert body["roles"] == ["OWNER"]
    assert body["display_name"] == "Owner Test"
    assert body["event"]["slug"] == "dryrun"
    assert body["csrf_token"]
    assert set(body) == {
        "user_id", "net_id", "display_name", "email", "roles", "event", "csrf_token"
    }


async def test_me_returns_visitor_for_an_unseeded_net_id(client, seeded_staff):
    await _dev_login(client, "random.student")
    response = await client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["roles"] == ["VISITOR"]


async def test_require_role_denies_a_visitor(client, seeded_staff):
    await _dev_login(client, "random.student")
    response = await client.get("/api/v1/me/owner-only")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_require_role_allows_the_owner(client, seeded_staff):
    await _dev_login(client, "owner.test")
    response = await client.get("/api/v1/me/owner-only")

    assert response.status_code == 200


async def test_require_role_denies_an_admin_from_an_owner_route(client, seeded_staff):
    await _dev_login(client, "admin.test")
    assert (await client.get("/api/v1/me/owner-only")).status_code == 403


async def test_login_writes_an_audit_row_and_a_funnel_row(client, db_session, seeded_staff):
    from app.models import AuditLog, FunnelEvent

    await _dev_login(client, "owner.test")

    audit = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "LOGIN")
    )
    funnel = await db_session.scalar(
        select(func.count()).select_from(FunnelEvent).where(FunnelEvent.step == "LOGIN")
    )
    assert audit == 1
    assert funnel == 1


async def test_audit_row_stores_no_raw_email(client, db_session, seeded_staff):
    from app.models import AuditLog

    await _dev_login(client, "owner.test")
    row = (await db_session.execute(select(AuditLog).where(AuditLog.action == "LOGIN"))).scalar_one()

    assert row.ip_hash is None or "@" not in row.ip_hash
    assert row.after is None or "owner@" not in str(row.after)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_me.py -v`
Expected: FAIL — 404 on `/api/v1/me`.

- [ ] **Step 3: Write `apps/api/app/schemas/me.py`**

```python
from uuid import UUID

from pydantic import BaseModel

from app.services.identity.roles import Role


class EventSummary(BaseModel):
    id: UUID
    slug: str
    name: str


class MeResponse(BaseModel):
    """Phase 0 decision 2.4. Single event, so no membership list."""

    user_id: UUID
    net_id: str
    display_name: str
    email: str | None
    roles: list[Role]
    event: EventSummary
    csrf_token: str
```

- [ ] **Step 4: Write `apps/api/app/services/audit.py`**

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, FunnelEvent


async def write_audit(
    db: AsyncSession,
    *,
    event_id: UUID | None,
    actor_user_id: UUID | None,
    actor_net_id: str | None,
    actor_role: str | None,
    action: str,
    object_type: str | None = None,
    object_id: UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip_hash: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append-only. Never store raw PII in `before`/`after` — hash it first."""
    db.add(
        AuditLog(
            event_id=event_id,
            actor_user_id=actor_user_id,
            actor_net_id=actor_net_id,
            actor_role=actor_role,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before=before,
            after=after,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
    )


async def write_funnel_event(
    db: AsyncSession, *, event_id: UUID, user_id: UUID | None, step: str
) -> None:
    """Which step each person reached. Unbackfillable, so it starts at M1."""
    db.add(FunnelEvent(event_id=event_id, user_id=user_id, step=step))
```

- [ ] **Step 5: Add `require_role` to `apps/api/app/core/deps.py`**

Append:

```python
from collections.abc import Callable

from app.services.identity.roles import Role


def require_role(*allowed: Role) -> Callable:
    """Deny by default. Every protected route declares its roles explicitly."""

    async def _dependency(session: SessionData = Depends(current_session)) -> SessionData:
        held = {Role(role) for role in session.roles}
        if not held & set(allowed):
            raise AppError(
                ErrorCode.FORBIDDEN,
                "You do not have access to this.",
                http_status=status.HTTP_403_FORBIDDEN,
            )
        return session

    return _dependency
```

- [ ] **Step 6: Write `apps/api/app/api/v1/routes/me.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, require_role
from app.schemas.me import EventSummary, MeResponse
from app.services.identity.roles import Role, get_current_event
from app.services.identity.sessions import SessionData

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    event = await get_current_event(db)
    return MeResponse(
        user_id=session.user_id,
        net_id=session.net_id,
        display_name=session.display_name,
        email=session.email,
        roles=[Role(role) for role in session.roles],
        event=EventSummary(id=event.id, slug=event.slug, name=event.name),
        csrf_token=session.csrf_token,
    )


@router.get("/me/owner-only")
async def owner_only(session: SessionData = Depends(require_role(Role.OWNER))) -> dict[str, str]:
    """Smoke route proving deny-by-default works end to end. Kept — it is the
    regression test for `require_role` and costs three lines."""
    return {"status": "ok"}
```

- [ ] **Step 7: Write the audit and funnel rows on login**

In `apps/api/app/api/v1/routes/auth.py`, inside `auth_callback`, replace `await db.commit()` with:

```python
    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=user.id,
        actor_net_id=user.net_id,
        actor_role=next((r.value for r in roles if r.value in {"OWNER", "ADMIN", "SECURITY"}), None),
        action="LOGIN",
        object_type="user",
        object_id=user.id,
        after={"provider": claims.auth_method, "roles": [r.value for r in roles]},
        ip_hash=hash_value(client_ip_prefix(request.client.host)) if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await write_funnel_event(db, event_id=event.id, user_id=user.id, step="LOGIN")
    await db.commit()
```

Add to the imports at the top of the file:

```python
from app.services.audit import write_audit, write_funnel_event
```

- [ ] **Step 8: Mount the router in `apps/api/app/main.py`**

```python
    from app.api.v1.routes import me as me_routes

    app.include_router(me_routes.router)
```

- [ ] **Step 9: Run the full suite**

Run: `docker compose run --rm api pytest tests/ -v`
Expected: every test passes — this is the M1 "done when" gate.

- [ ] **Step 10: Verify the acceptance criteria by hand**

```bash
docker compose down -v && docker compose up -d --build
docker compose exec api alembic upgrade head
make seed
```
Then in a browser: sign in as `owner.test` → `/api/v1/me` shows `"roles": ["OWNER"]`.
Sign out, sign in as `visitor.test` → `/api/v1/me` shows `"roles": ["VISITOR"]`.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(m1): /me endpoint, deny-by-default role dependency, login audit trail"
```

---

## Task 7: `EntraAuthProvider` — real Microsoft OIDC

**Deliverable:** setting `AUTH_PROVIDER=entra` with a real app registration produces a working Microsoft login, with full ID-token validation. Tested without Azure by signing tokens with a locally generated key.

**Files:**
- Create: `apps/api/app/services/identity/entra.py`
- Test: `apps/api/tests/test_auth_entra.py`

**Interfaces:**
- Consumes: `IdentityClaims`, `AuthProvider` protocol (Task 5), `get_settings()`.
- Produces: `app.services.identity.entra.EntraAuthProvider`, `app.services.identity.entra.JwksCache`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_auth_entra.py`:

```python
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
KID = "test-key-1"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def entra_settings(monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "shhh")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def make_id_token(rsa_key, **overrides):
    now = int(time.time())
    payload = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": "aaaabbbb-cccc-dddd-eeee-ffff00001111",
        "preferred_username": "Aarav.Sharma@university.edu",
        "name": "Aarav Sharma",
        "email": "aarav.sharma@university.edu",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def patched_provider(monkeypatch, rsa_key, entra_settings):
    """Stub the two network calls: JWKS fetch and the token exchange."""
    from app.services.identity import entra as entra_module

    async def fake_signing_key(self, kid: str):
        assert kid == KID
        return rsa_key.public_key()

    monkeypatch.setattr(entra_module.JwksCache, "signing_key", fake_signing_key)
    return entra_module


async def test_authorization_url_carries_pkce_and_tenant(patched_provider):
    provider = patched_provider.EntraAuthProvider()
    stored = await provider.state_payload()
    url = await provider.authorization_url(state="abc", next_url="/me", stored=stored)

    assert f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    assert "state=abc" in url
    assert f"client_id={CLIENT}" in url
    assert "verifier" in stored and "nonce" in stored


async def test_valid_token_yields_claims(monkeypatch, patched_provider, rsa_key):
    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(rsa_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    claims = await provider.exchange(
        params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
    )

    assert claims.subject == "aaaabbbb-cccc-dddd-eeee-ffff00001111"
    assert claims.net_id == "aarav.sharma@university.edu"
    assert claims.display_name == "Aarav Sharma"
    assert claims.auth_method == "MICROSOFT_SSO"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "someone-else"}, "audience"),
        ({"tid": "99999999-9999-9999-9999-999999999999"}, "tenant"),
        ({"iss": "https://evil.example/v2.0"}, "issuer"),
        ({"exp": int(time.time()) - 10, "iat": int(time.time()) - 3600,
          "nbf": int(time.time()) - 3600}, "expiry"),
        ({"nonce": "wrong-nonce"}, "nonce"),
    ],
)
async def test_invalid_tokens_are_rejected(
    monkeypatch, patched_provider, rsa_key, overrides, reason
):
    from app.core.errors import AppError

    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(rsa_key, **overrides)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID", reason


async def test_token_signed_by_the_wrong_key_is_rejected(monkeypatch, patched_provider):
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    from app.core.errors import AppError

    attacker_key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(attacker_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError):
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )


async def test_missing_code_is_rejected(patched_provider):
    from app.core.errors import AppError

    provider = patched_provider.EntraAuthProvider()
    with pytest.raises(AppError):
        await provider.exchange(params={}, stored={"verifier": "v", "nonce": "n"})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_auth_entra.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.identity.entra'`.

- [ ] **Step 3: Write `apps/api/app/services/identity/entra.py`**

```python
"""Microsoft Entra ID — OIDC Authorization Code + PKCE (LLD-v1 §8.1).

We never keep Microsoft access or refresh tokens and make no Graph calls at
runtime. The ID token is validated once, then our own session takes over.
"""
import base64
import hashlib
import time
from collections.abc import Mapping
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from fastapi import status

from app.core.errors import AppError, ErrorCode
from app.core.security import new_token
from app.core.settings import get_settings
from app.services.identity.provider import IdentityClaims

log = structlog.get_logger()

JWKS_TTL_SECONDS = 43_200  # 12h
CLOCK_SKEW_SECONDS = 120


class JwksCache:
    """Caches Microsoft's signing keys; refreshes on a `kid` miss."""

    def __init__(self) -> None:
        self._keys: dict[str, object] = {}
        self._fetched_at: float = 0.0

    @property
    def _url(self) -> str:
        return (
            f"https://login.microsoftonline.com/"
            f"{get_settings().entra_tenant_id}/discovery/v2.0/keys"
        )

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            document = response.json()
        self._keys = {
            key["kid"]: jwt.PyJWK(key).key for key in document.get("keys", []) if "kid" in key
        }
        self._fetched_at = time.monotonic()

    async def signing_key(self, kid: str):
        stale = (time.monotonic() - self._fetched_at) > JWKS_TTL_SECONDS
        if kid not in self._keys or stale:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        return key


_jwks = JwksCache()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class EntraAuthProvider:
    name = "entra"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._jwks = _jwks

    @property
    def _authority(self) -> str:
        return f"https://login.microsoftonline.com/{self._settings.entra_tenant_id}"

    async def state_payload(self) -> dict:
        return {"verifier": new_token(48), "nonce": new_token(16)}

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        settings = self._settings
        query = {
            "client_id": settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "response_mode": "query",
            "scope": "openid profile email",
            "state": state,
            "nonce": stored["nonce"],
            "code_challenge": _pkce_challenge(stored["verifier"]),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if settings.allowed_domains:
            query["domain_hint"] = settings.allowed_domains[0]
        return f"{self._authority}/oauth2/v2.0/authorize?{urlencode(query)}"

    async def _exchange_code(self, code: str, verifier: str) -> str:
        settings = self._settings
        payload = {
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "code_verifier": verifier,
            "scope": "openid profile email",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self._authority}/oauth2/v2.0/token", data=payload)
        if response.status_code != 200:
            log.warning("entra_token_exchange_failed", status_code=response.status_code)
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That sign-in could not be completed. Try again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        id_token = response.json().get("id_token")
        if not id_token:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That sign-in could not be completed. Try again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        return id_token

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        code = params.get("code")
        if not code:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                params.get("error_description") or "Sign-in was cancelled.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )

        id_token = await self._exchange_code(code, stored["verifier"])
        settings = self._settings

        try:
            header = jwt.get_unverified_header(id_token)
            key = await self._jwks.signing_key(header["kid"])
            payload = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=settings.entra_client_id,
                issuer=f"{self._authority}/v2.0",
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except AppError:
            raise
        except Exception as exc:
            log.warning("entra_id_token_invalid", error_type=type(exc).__name__)
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            ) from exc

        if payload.get("tid") != settings.entra_tenant_id:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That account is not from this university's tenant.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        if stored.get("nonce") and payload.get("nonce") != stored["nonce"]:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )

        net_id = (
            payload.get("preferred_username") or payload.get("upn") or payload.get("email") or ""
        ).strip().lower()
        return IdentityClaims(
            subject=payload["oid"],
            net_id=net_id,
            email=(payload.get("email") or net_id) or None,
            display_name=payload.get("name") or net_id,
            auth_method="MICROSOFT_SSO",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm api pytest tests/test_auth_entra.py -v`
Expected: 9 passed.

- [ ] **Step 5: Document the Azure setup without performing it**

Append to `.env.example`:

```env
# ── Entra setup (the ONLY thing that needs a real cloud account) ──────────
# Azure Portal → App registrations → New registration
#   Redirect URI (Web): http://localhost:8000/auth/callback
#   Certificates & secrets → New client secret → copy into ENTRA_CLIENT_SECRET
#   Token configuration → add optional claim: email (ID token)
# Then set AUTH_PROVIDER=entra. Leave it as `dev` to work without Azure.
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(m1): entra oidc provider with pkce, jwks cache and full token validation"
```

---

## Task 8: Storage and mail seams

**Deliverable:** an `ObjectStore` that writes to fake-gcs locally and real GCS in cloud, and a `Mailer` that reaches mailpit locally and Workspace SMTP in cloud — both chosen by settings, both proven against the compose services. Nothing in M1 calls them; M3 and M4 plug straight in.

> **Scope note:** no OCR seam. There is no implementation to put behind it until M4, and a Protocol with no implementation and no test is scaffolding, not a seam.

**Files:**
- Create: `apps/api/app/services/storage/provider.py`, `apps/api/app/services/storage/local.py`, `apps/api/app/services/storage/gcs.py`
- Create: `apps/api/app/services/mailer/provider.py`, `apps/api/app/services/mailer/smtp.py`
- Test: `apps/api/tests/test_seams.py`

**Interfaces:**
- Produces:
  - `app.services.storage.provider.ObjectStore` (Protocol: `put`, `get`, `signed_url`, `delete`, `exists`), `get_object_store() -> ObjectStore`
  - `app.services.storage.local.LocalObjectStore`, `app.services.storage.gcs.GcsObjectStore`
  - `app.services.mailer.provider.EmailMessage` (Pydantic: `to`, `subject`, `text_body`, `html_body`), `Mailer` Protocol, `get_mailer() -> Mailer`
  - `app.services.mailer.smtp.SmtpMailer`

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_seams.py`:

```python
import httpx
import pytest


@pytest.fixture
def store(settings):
    from app.services.storage.provider import get_object_store

    return get_object_store()


async def test_local_backend_is_selected_in_dev(store):
    from app.services.storage.local import LocalObjectStore

    assert isinstance(store, LocalObjectStore)


async def test_put_then_get_round_trips(store):
    key = "test/hello.txt"
    await store.put(key, b"hello unigate", "text/plain")
    assert await store.get(key) == b"hello unigate"
    assert await store.exists(key) is True


async def test_delete_removes_the_object(store):
    key = "test/temp.txt"
    await store.put(key, b"x", "text/plain")
    await store.delete(key)
    assert await store.exists(key) is False


async def test_missing_object_is_not_present(store):
    assert await store.exists("test/never-written.txt") is False


async def test_signed_url_is_fetchable(store):
    key = "test/signed.txt"
    await store.put(key, b"signed payload", "text/plain")
    url = await store.signed_url(key, expires_seconds=90)

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert response.content == b"signed payload"


async def test_gcs_backend_is_selected_when_configured(monkeypatch):
    from app.core.settings import get_settings
    from app.services.storage.gcs import GcsObjectStore
    from app.services.storage.provider import get_object_store

    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    get_settings.cache_clear()
    assert isinstance(get_object_store(), GcsObjectStore)
    get_settings.cache_clear()


async def test_mail_lands_in_mailpit(settings):
    from app.services.mailer.provider import EmailMessage, get_mailer

    async with httpx.AsyncClient(timeout=10) as client:
        await client.delete("http://mailpit:8025/api/v1/messages")

    await get_mailer().send(
        EmailMessage(
            to="aarav@university.edu",
            subject="Your ticket is ready",
            text_body="Log in at http://localhost:8000/",
        )
    )

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("http://mailpit:8025/api/v1/messages")
    messages = response.json()["messages"]

    assert len(messages) == 1
    assert messages[0]["Subject"] == "Your ticket is ready"
    assert messages[0]["To"][0]["Address"] == "aarav@university.edu"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm api pytest tests/test_seams.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.storage.provider'`.

- [ ] **Step 3: Write `apps/api/app/services/storage/provider.py`**

```python
"""Object storage seam.

`local`  — fake-gcs-server. No credentials, no signing; URLs are plain reads.
`gcs`    — real Google Cloud Storage with V4 signed URLs.

Two implementations rather than one endpoint override because signing genuinely
differs: locally there is nothing to sign against.
"""
from typing import Protocol, runtime_checkable

from app.core.settings import StorageBackend, get_settings


@runtime_checkable
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def signed_url(self, key: str, *, expires_seconds: int = 90) -> str: ...


def get_object_store() -> ObjectStore:
    from app.services.storage.gcs import GcsObjectStore
    from app.services.storage.local import LocalObjectStore

    registry: dict[StorageBackend, type[ObjectStore]] = {
        StorageBackend.LOCAL: LocalObjectStore,
        StorageBackend.GCS: GcsObjectStore,
    }
    missing = set(StorageBackend) - set(registry)
    if missing:
        raise RuntimeError(f"ObjectStore registry is missing implementations for {missing}")
    return registry[get_settings().storage_backend]()
```

- [ ] **Step 4: Write `apps/api/app/services/storage/local.py`**

```python
"""fake-gcs-server backed store. Speaks the GCS JSON API, needs no credentials."""
from urllib.parse import quote

import httpx

from app.core.settings import get_settings


class LocalObjectStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint = settings.storage_endpoint_url.rstrip("/")
        self._public = settings.storage_public_url.rstrip("/")
        self._bucket = settings.storage_bucket

    def _object_url(self, key: str) -> str:
        return f"{self._endpoint}/storage/v1/b/{self._bucket}/o/{quote(key, safe='')}"

    async def _ensure_bucket(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{self._endpoint}/storage/v1/b/{self._bucket}")
        if response.status_code == 404:
            await client.post(
                f"{self._endpoint}/storage/v1/b", json={"name": self._bucket}
            )

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            await self._ensure_bucket(client)
            response = await client.post(
                f"{self._endpoint}/upload/storage/v1/b/{self._bucket}/o",
                params={"uploadType": "media", "name": key},
                content=data,
                headers={"Content-Type": content_type},
            )
            response.raise_for_status()
        return key

    async def get(self, key: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(self._object_url(key), params={"alt": "media"})
            response.raise_for_status()
            return response.content

    async def exists(self, key: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            return (await client.get(self._object_url(key))).status_code == 200

    async def delete(self, key: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(self._object_url(key))
            if response.status_code not in (200, 204, 404):
                response.raise_for_status()

    async def signed_url(self, key: str, *, expires_seconds: int = 90) -> str:
        # Local only: there is no signer. Same shape, no security claim.
        return f"{self._public}/storage/v1/b/{self._bucket}/o/{quote(key, safe='')}?alt=media"
```

- [ ] **Step 5: Write `apps/api/app/services/storage/gcs.py`**

```python
"""Real GCS. The client library is synchronous, so every call goes to a thread —
CLAUDE.md: blocking work never runs inline in a request."""
from datetime import timedelta

import anyio
from google.cloud import storage

from app.core.settings import get_settings


class GcsObjectStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket_name = settings.storage_bucket
        self._client: storage.Client | None = None

    def _bucket(self):
        if self._client is None:
            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        def _put() -> None:
            self._bucket().blob(key).upload_from_string(data, content_type=content_type)

        await anyio.to_thread.run_sync(_put)
        return key

    async def get(self, key: str) -> bytes:
        return await anyio.to_thread.run_sync(lambda: self._bucket().blob(key).download_as_bytes())

    async def exists(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(lambda: self._bucket().blob(key).exists())

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            blob = self._bucket().blob(key)
            if blob.exists():
                blob.delete()

        await anyio.to_thread.run_sync(_delete)

    async def signed_url(self, key: str, *, expires_seconds: int = 90) -> str:
        """V4 signed URL. The bucket stays private — this is the only read path."""

        def _sign() -> str:
            return self._bucket().blob(key).generate_signed_url(
                version="v4", expiration=timedelta(seconds=expires_seconds), method="GET"
            )

        return await anyio.to_thread.run_sync(_sign)
```

- [ ] **Step 6: Write the mailer**

`apps/api/app/services/mailer/provider.py`:

```python
"""Mail seam. ONE implementation: mailpit and Google Workspace both speak SMTP,
so the difference is host, TLS and credentials — configuration, not code."""
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, EmailStr


class EmailMessage(BaseModel):
    to: EmailStr
    subject: str
    text_body: str
    html_body: str | None = None


@runtime_checkable
class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


def get_mailer() -> Mailer:
    from app.services.mailer.smtp import SmtpMailer

    return SmtpMailer()
```

`apps/api/app/services/mailer/smtp.py`:

```python
from email.message import EmailMessage as MimeMessage

import aiosmtplib
import structlog

from app.core.settings import get_settings
from app.services.mailer.provider import EmailMessage

log = structlog.get_logger()


class SmtpMailer:
    async def send(self, message: EmailMessage) -> None:
        settings = get_settings()
        mime = MimeMessage()
        mime["From"] = settings.smtp_from
        mime["To"] = str(message.to)
        mime["Subject"] = message.subject
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        await aiosmtplib.send(
            mime,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=settings.smtp_use_tls or None,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
        )
        # `to` is in PII_KEYS, so this renders as a hash.
        log.info("email_sent", to=str(message.to), template=message.subject)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `docker compose run --rm api pytest tests/test_seams.py -v`
Expected: 7 passed. Check the mail landed: `open http://localhost:8025`.

- [ ] **Step 8: Run the whole suite and the linter**

```bash
docker compose run --rm api pytest tests/ -v
docker compose run --rm api ruff check .
```
Expected: all green. `ruff` must report no `S608` (f-string SQL) findings.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(m1): object storage and mail seams with local and cloud implementations"
```

---

## M1 acceptance — run this before declaring the milestone done

```bash
docker compose down -v
docker compose up -d --build
docker compose exec api alembic upgrade head     # clean from empty
make seed
docker compose run --rm api pytest tests/ -v     # everything green
docker compose run --rm api ruff check .
curl -s localhost:8000/healthz
curl -s localhost:8000/readyz
```

Then in a browser:

1. `http://localhost:8000/` → login page renders.
2. Sign in as `owner.test` → `http://localhost:8000/api/v1/me` shows `"roles": ["OWNER"]`.
3. `http://localhost:8000/api/v1/me/owner-only` → 200.
4. Log out, sign in as `visitor.test` → `/api/v1/me` shows `"roles": ["VISITOR"]`.
5. `http://localhost:8000/api/v1/me/owner-only` → 403 with `{"error":{"code":"FORBIDDEN",…}}`.
6. `docker compose logs api | grep -c '@'` → **0**. No raw email ever reaches a log line.

Every box above must be ticked before M2 starts (CLAUDE.md build order).

---

## Self-review

**Spec coverage** — LLD-v2 §11 M1 row is "compose stack, settings, Postgres/Alembic, Microsoft SSO, sessions, roles seeded, `/me`":

| Requirement | Task |
|---|---|
| compose stack (postgres, redis, mailpit, fake-gcs) | T1 |
| settings | T1 |
| Postgres / Alembic | T2 |
| Microsoft SSO | T7 (behind the T5 seam) |
| sessions | T4 |
| roles seeded | T3 + T6 |
| `/me` | T6 |
| "log in as OWNER, a test NetID logs in as VISITOR" | T5 + T6, verified in M1 acceptance |
| §13 security floor: HttpOnly/Secure/SameSite cookies, CSRF, deny-by-default role dependency, no PII in logs, audit rows | T1 (logging), T4 (cookies + CSRF), T6 (`require_role`, audit) |
| Addendum §A1 `event_inventory` + `no_oversell` | T2 (table + CHECK), T3 (seeded) |

**Known gaps, deliberately deferred and recorded here so M2 does not rediscover them:**
- `PARTICIPANT` is never returned by `resolve_roles` in M1. It cannot be — `tickets` does not exist yet. M2 adds the lookup.
- Session pinning to `ip_hash` / `ua_hash` is *stored* but not *checked*. The soft re-auth rule (LLD-v1 §8.2) needs a re-auth flow that does not exist until there is something to protect. M2.
- `inventory_ledger` and the ledger trigger are M5. `event_inventory` exists in M1 only because the seed needs a capacity number and the CHECK is free.
- Storage `signed_url` on the local backend is not signed. Correct for a local emulator; the GCS implementation is the one that carries the security property.

**Placeholder scan:** no TBDs, no "add error handling", no "similar to Task N". Every code step carries the full file or the exact block to append.

**Type consistency:** `IdentityClaims` fields (`subject`, `net_id`, `email`, `display_name`, `auth_method`) are identical in T5 (`dev.py`) and T7 (`entra.py`). `AuthProvider.exchange(*, params, stored)` matches both implementations and the T5 call site. `SessionData` field names used in T5's `create_session(...)` call match T4's definition. `Role` values are asserted at import to be a superset of `StaffRole` values.
