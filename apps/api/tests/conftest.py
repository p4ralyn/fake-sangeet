import asyncio
import os
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TEST_ENV = {
    "APP_ENV": "dev",
    "SECRET_KEY": "test-secret-key-not-for-production-use",
    "DATABASE_URL": "postgresql+asyncpg://unigate:unigate@postgres:5432/unigate_test",
    "REDIS_URL": "redis://redis:6379/1",
    "AUTH_PROVIDER": "dev",
    "ALLOWED_DOMAINS": '["university.edu"]',
    "EVENT_SLUG": "dryrun",
    # .env.example points this at localhost:4443 for a real browser; inside the
    # api container (where tests run) that's unreachable, so tests fetch signed
    # URLs over the compose network instead. See the comment in .env.example.
    "STORAGE_PUBLIC_URL": "http://fake-gcs:4443",
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
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
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
