"""Object storage seam.

`local`  — fake-gcs-server. No credentials, needs none.
`gcs`    — real Google Cloud Storage.

Two adapters rather than one endpoint override because credentials and
transport genuinely differ, and because the local one has to work with no
cloud account at all — `docker compose up` is the whole dev environment.

**`signed_url` used to be the third method here and is gone.** It was the
reason the seam was drawn, and it turned out to be the one thing that could
not work: the local backend cannot sign, so it returned its own in-compose
address, which no browser can fetch. ID photos are served through the API
instead (`services/verification/links.py`), and nothing called `signed_url`
by the time it was deleted.

`exists` and `delete` have no caller yet either, but they stay: the T+45
purge of ID images is a standing guardrail, and that job needs both.
"""
from typing import Protocol, runtime_checkable

from app.core.settings import StorageBackend, get_settings


@runtime_checkable
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...


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
