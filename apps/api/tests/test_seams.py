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
