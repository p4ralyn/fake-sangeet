"""Decision tokens — the thing that stops two gates racing one pass.

LLD-v1 §11.2. When the server cannot decide the transition on its own it hands
the operator a list of legal actions plus a short-lived, single-use token bound
to *that* scan attempt. The action the operator taps is only applied if it
arrives back with that token. A second gate scanning the same pass in the
meantime gets its own attempt and its own token, and whichever confirm lands
first wins under the row lock; the loser is refused with the real reason.

Single-use is enforced by GETDEL, which is atomic — two confirms racing the
same token cannot both read it.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.security import new_token
from app.core.settings import get_settings
from app.models.access import ScanAction

DECISION_PREFIX = "scan:decision:"
OUTCOME_SUFFIX = ":outcome"


class DecisionRecord(BaseModel):
    """Everything the confirm step needs, bound at issue time so the phone
    cannot substitute a different pass, gate or operator on the way back."""

    event_id: UUID
    pass_id: UUID
    serial: int
    legal_actions: list[ScanAction]
    gate_id: UUID | None
    device_id: UUID | None
    client_scan_uuid: UUID
    occurred_at: datetime
    offline: bool
    scanned_by: UUID | None
    # sha256 of the presented token, never the token. Carried so the scan row
    # written at confirm time has the same forensic fingerprint as a denial.
    token_hash: str | None = None


def _key(token: str) -> str:
    return f"{DECISION_PREFIX}{token}"


def _outcome_key(token: str) -> str:
    return f"{DECISION_PREFIX}{token}{OUTCOME_SUFFIX}"


async def issue_decision(redis: Redis, record: DecisionRecord) -> tuple[str, int]:
    """Returns (token, ttl_seconds)."""
    ttl = get_settings().scan_decision_token_ttl_seconds
    token = new_token(24)
    await redis.set(_key(token), record.model_dump_json(), ex=ttl)
    return token, ttl


async def consume_decision(redis: Redis, token: str) -> DecisionRecord | None:
    """Atomically take the token. A second caller gets None."""
    raw = await redis.getdel(_key(token))
    if raw is None:
        return None
    return DecisionRecord.model_validate_json(raw)


async def remember_outcome(redis: Redis, token: str, scan_id: UUID) -> None:
    """So a double-tapped confirm returns the result it already produced
    instead of `DECISION_TOKEN_EXPIRED` — an error screen at a gate makes
    operators rescan and panic (§11.2)."""
    settings = get_settings()
    ttl = max(settings.scan_duplicate_window_seconds, settings.scan_decision_token_ttl_seconds)
    await redis.set(_outcome_key(token), str(scan_id), ex=ttl)


async def recall_outcome(redis: Redis, token: str) -> UUID | None:
    raw = await redis.get(_outcome_key(token))
    return UUID(raw) if raw else None
