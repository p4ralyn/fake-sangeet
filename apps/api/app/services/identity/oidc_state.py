"""Single-use OIDC state, stashed in Redis alongside provider-specific values
(PKCE, nonce) and the post-login destination (LLD-v1 §10.1).

`consume_state` uses GETDEL so a state is valid for exactly one exchange — a
replayed state finds nothing in Redis and the callback fails closed with
OIDC_STATE_INVALID.
"""
import json

from redis.asyncio import Redis

from app.core.security import new_token

STATE_PREFIX = "oidc:state:"


async def start_state(redis: Redis, payload: dict, *, ttl_seconds: int) -> str:
    state = new_token(24)
    await redis.set(f"{STATE_PREFIX}{state}", json.dumps(payload), ex=ttl_seconds)
    return state


async def consume_state(redis: Redis, state: str) -> dict | None:
    if not state:
        return None
    raw = await redis.getdel(f"{STATE_PREFIX}{state}")
    return json.loads(raw) if raw is not None else None
