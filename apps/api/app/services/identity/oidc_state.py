"""Single-use OIDC state, stashed in Redis alongside provider-specific values
(PKCE, nonce) and the post-login destination (LLD-v1 §10.1).

`consume_state` uses GETDEL so a state is valid for exactly one exchange — a
replayed state finds nothing in Redis and the callback fails closed with
OIDC_STATE_INVALID.
"""
import json
from urllib.parse import urlsplit

from redis.asyncio import Redis

from app.core.security import new_token

STATE_PREFIX = "oidc:state:"
DEFAULT_NEXT = "/api/v1/me"


def safe_next(candidate: str | None) -> str:
    """Only ever redirect to a same-origin, relative path.

    `next=//evil.example` is scheme-relative -- browsers resolve a leading
    `//` to an absolute URL on another host. `next=/\\evil.example` resolves
    to the same thing: per the WHATWG URL spec, browsers normalise a
    backslash to a forward slash for http(s) URLs, so `/\\evil.example`
    becomes `//evil.example` before the request is ever made. Both must be
    rejected alongside anything carrying an explicit scheme or host.
    """
    if not candidate or not candidate.startswith("/"):
        return DEFAULT_NEXT
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return DEFAULT_NEXT
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return DEFAULT_NEXT
    return candidate


async def start_state(redis: Redis, payload: dict, *, ttl_seconds: int) -> str:
    state = new_token(24)
    await redis.set(f"{STATE_PREFIX}{state}", json.dumps(payload), ex=ttl_seconds)
    return state


async def consume_state(redis: Redis, state: str) -> dict | None:
    if not state:
        return None
    raw = await redis.getdel(f"{STATE_PREFIX}{state}")
    return json.loads(raw) if raw is not None else None
