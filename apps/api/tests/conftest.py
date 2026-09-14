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
