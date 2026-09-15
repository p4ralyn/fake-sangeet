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
