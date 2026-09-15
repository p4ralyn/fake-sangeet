"""Process-wide pooled HTTP client.

Mirrors `get_redis_client()` in `app/core/deps.py`: a lazily-created,
module-level singleton so outbound calls (the Entra JWKS fetch and token
exchange today; the storage adapter next) share one connection pool instead
of opening a fresh one per call. `aclose_http_client()` resets the global
back to `None` so the accessor safely recreates it afterwards -- the test
suite builds the app repeatedly without running the lifespan that would
otherwise be the only thing closing it.
"""
import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def aclose_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
