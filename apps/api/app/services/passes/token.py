"""QR pass token — mint and verify. LLD-v1 §10.1.

    UG1.<base64url(payload)>.<base64url(sig)>

`payload` is CBOR (smaller than JSON → lower QR density → faster scan in bad
light) holding `{v, e, p, s, x}`: version, event uuid bytes, pass uuid bytes,
serial, expiry. `sig` is HMAC-SHA256 truncated to 128 bits.

Pure: no database, no network, no clock beyond `now`. Everything here is
unit-testable without a fixture.

Why HMAC and not a JWT: verification has to work offline on a phone at a door
with no signal, against a key that can be scoped to one gate and one day.

**A token is a bearer credential.** Never log one, never put one in an error
message, never store it — store `sha256(token)` when you need forensics.
"""
import hashlib
import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from uuid import UUID

import cbor2
from pydantic import BaseModel

from app.core.errors import ErrorCode
from app.core.settings import get_settings

TOKEN_PREFIX = "UG1"  # noqa: S105 -- the wire format marker, not a secret
TOKEN_VERSION = 1
SIGNATURE_BYTES = 16  # HMAC-SHA256 truncated to 128 bits (§10.1)

# Domain separation: a signature minted here can never be replayed as one for
# some other HMAC use of the same master key.
_KEY_INFO = b"unigate/pass-token/v1/"
_SIG_CONTEXT = b"UG1."

# `x` is a uint32 epoch second per §10.1 — it runs out in 2106, long after this
# event. Reject anything that would silently wrap instead of encoding it.
_MAX_EXPIRY = 2**32 - 1
_MAX_SERIAL = 2**16 - 1


class PassTokenError(Exception):
    """Carries the stable §13.6 code the scanner UI branches on."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PassTokenClaims(BaseModel):
    """What a *cryptographically valid* token asserts. Not yet checked against
    the database: `serial` still has to match the pass's current serial, which
    only the locked row knows (see services/access/scans.py)."""

    version: int
    event_id: UUID
    pass_id: UUID
    serial: int
    expires_at: datetime


def _b64encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def _event_key(event_id: UUID) -> bytes:
    """Per-event signing key derived from the master secret.

    v1 §10.1 wants a key per event in Secret Manager. Deriving it keeps that
    property — a token minted for one event cannot verify under another event's
    key — without a secret store in a single-event dry run.
    """
    master = get_settings().pass_signing_key.encode("utf-8")
    return hmac.new(master, _KEY_INFO + event_id.bytes, hashlib.sha256).digest()


def _sign(event_id: UUID, payload_b64: str) -> str:
    digest = hmac.new(
        _event_key(event_id), _SIG_CONTEXT + payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64encode(digest[:SIGNATURE_BYTES])


def token_fingerprint(token: str) -> str:
    """Log- and database-safe stand-in for the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_pass_token(
    *, event_id: UUID, pass_id: UUID, serial: int, expires_at: datetime
) -> str:
    if not 1 <= serial <= _MAX_SERIAL:
        raise ValueError(f"serial must fit a uint16, got {serial}")
    expiry = int(expires_at.timestamp())
    if not 0 < expiry <= _MAX_EXPIRY:
        raise ValueError("expires_at does not fit the uint32 expiry field")

    payload = cbor2.dumps(
        {
            "v": TOKEN_VERSION,
            "e": event_id.bytes,
            "p": pass_id.bytes,
            "s": serial,
            "x": expiry,
        }
    )
    payload_b64 = _b64encode(payload)
    return f"{TOKEN_PREFIX}.{payload_b64}.{_sign(event_id, payload_b64)}"


def _decode_payload(payload_b64: str) -> dict:
    try:
        payload = cbor2.loads(_b64decode(payload_b64))
    except Exception as exc:  # noqa: BLE001 -- any decode failure is one answer
        raise PassTokenError(ErrorCode.INVALID_TOKEN, "Unreadable pass code.") from exc
    if not isinstance(payload, dict):
        raise PassTokenError(ErrorCode.INVALID_TOKEN, "Unreadable pass code.")
    return payload


def verify_pass_token(
    token: str, *, expected_event_id: UUID, now: datetime | None = None
) -> PassTokenClaims:
    """Structure, signature, event and expiry. Raises `PassTokenError`.

    The signing key is derived from the event id *in the payload*, not from the
    expected one, so that a genuine token for a different event fails as
    `TOKEN_WRONG_EVENT` rather than being indistinguishable from a forgery.
    Deriving a key from attacker-chosen bytes is safe here: the master secret
    is still required to produce a matching MAC, and the event is then checked
    against the one we actually run.
    """
    now = now or datetime.now(UTC)

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise PassTokenError(ErrorCode.INVALID_TOKEN, "That is not a pass code.")
    _, payload_b64, signature = parts

    payload = _decode_payload(payload_b64)
    try:
        version = int(payload["v"])
        event_id = UUID(bytes=payload["e"])
        pass_id = UUID(bytes=payload["p"])
        serial = int(payload["s"])
        expires_at = datetime.fromtimestamp(int(payload["x"]), tz=UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise PassTokenError(ErrorCode.INVALID_TOKEN, "Unreadable pass code.") from exc

    if version != TOKEN_VERSION:
        raise PassTokenError(ErrorCode.INVALID_TOKEN, "Unsupported pass code version.")

    # compare_digest, never ==: a timing side channel here leaks the MAC one
    # byte at a time, and a gate is exactly where someone has time to try.
    if not hmac.compare_digest(_sign(event_id, payload_b64), signature):
        raise PassTokenError(ErrorCode.INVALID_SIGNATURE, "This pass code is not genuine.")

    if event_id != expected_event_id:
        raise PassTokenError(ErrorCode.TOKEN_WRONG_EVENT, "This pass is for a different event.")

    if expires_at <= now:
        raise PassTokenError(ErrorCode.PASS_EXPIRED, "This pass has expired.")

    return PassTokenClaims(
        version=version,
        event_id=event_id,
        pass_id=pass_id,
        serial=serial,
        expires_at=expires_at,
    )
