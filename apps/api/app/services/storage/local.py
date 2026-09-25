"""fake-gcs-server backed store. Speaks the GCS JSON API, needs no credentials.

Uses the process-wide pooled HTTP client (`app.core.http.get_http_client`) rather
than opening a client per call, per CLAUDE.md ("httpx.AsyncClient from a shared
pooled client"). Each request still carries its own `timeout=` so the brief's
original per-call timeouts (30s for put/get, 10s for exists/delete/bucket-check)
are preserved even though the client itself is long-lived.
"""
from urllib.parse import quote

from app.core.http import get_http_client
from app.core.settings import get_settings


class LocalObjectStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint = settings.storage_endpoint_url.rstrip("/")
        self._public = settings.storage_public_url.rstrip("/")
        self._bucket = settings.storage_bucket

    def _object_url(self, key: str) -> str:
        return f"{self._endpoint}/storage/v1/b/{self._bucket}/o/{quote(key, safe='')}"

    async def _ensure_bucket(self) -> None:
        client = get_http_client()
        response = await client.get(
            f"{self._endpoint}/storage/v1/b/{self._bucket}", timeout=10
        )
        if response.status_code == 404:
            await client.post(
                f"{self._endpoint}/storage/v1/b", json={"name": self._bucket}, timeout=10
            )

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        client = get_http_client()
        await self._ensure_bucket()
        response = await client.post(
            f"{self._endpoint}/upload/storage/v1/b/{self._bucket}/o",
            params={"uploadType": "media", "name": key},
            content=data,
            headers={"Content-Type": content_type},
            timeout=30,
        )
        response.raise_for_status()
        return key

    async def get(self, key: str) -> bytes:
        client = get_http_client()
        response = await client.get(
            self._object_url(key), params={"alt": "media"}, timeout=30
        )
        response.raise_for_status()
        return response.content

    async def exists(self, key: str) -> bool:
        client = get_http_client()
        response = await client.get(self._object_url(key), timeout=10)
        return response.status_code == 200

    async def delete(self, key: str) -> None:
        client = get_http_client()
        response = await client.delete(self._object_url(key), timeout=10)
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()
