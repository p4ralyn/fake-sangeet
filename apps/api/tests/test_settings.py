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


def test_google_requires_credentials(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "google")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="requires GOOGLE_"):
        get_settings()


def test_google_defaults_allowed_domain_to_snu(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "c")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
    get_settings.cache_clear()
    assert get_settings().google_allowed_domain == "snu.edu.in"
