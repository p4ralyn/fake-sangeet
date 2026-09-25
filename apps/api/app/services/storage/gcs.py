"""Real GCS. The client library is synchronous, so every call goes to a thread —
CLAUDE.md: blocking work never runs inline in a request."""

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
