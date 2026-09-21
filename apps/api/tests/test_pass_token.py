"""QR token mint/verify — LLD-v1 §10.1.

Pure unit tests: no database, no app, no fixtures beyond the settings env.
"""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import cbor2
import pytest

from app.core.errors import ErrorCode
from app.services.passes.token import (
    PassTokenError,
    mint_pass_token,
    token_fingerprint,
    verify_pass_token,
)

EVENT = uuid4()
PASS = uuid4()


def _future(hours: int = 12) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _mint(*, event_id=EVENT, pass_id=PASS, serial=1, expires_at=None) -> str:
    return mint_pass_token(
        event_id=event_id,
        pass_id=pass_id,
        serial=serial,
        expires_at=expires_at or _future(),
    )


def test_round_trip_returns_every_claim(settings):
    expires_at = _future()
    claims = verify_pass_token(
        _mint(serial=7, expires_at=expires_at), expected_event_id=EVENT
    )

    assert claims.version == 1
    assert claims.event_id == EVENT
    assert claims.pass_id == PASS
    assert claims.serial == 7
    # uint32 epoch seconds — sub-second precision is not carried, by design.
    assert claims.expires_at == expires_at.replace(microsecond=0)


def test_token_has_the_ug1_three_part_shape(settings):
    prefix, payload, signature = _mint().split(".")
    assert prefix == "UG1"
    assert payload and signature
    # HMAC-SHA256 truncated to 128 bits = 16 bytes = 22 base64url chars unpadded.
    assert len(signature) == 22


def test_token_stays_short_enough_for_a_low_density_qr(settings):
    """§10.1 budgets "≈78 chars → QR version 5, ECC M". That figure is not
    reachable: two 16-byte UUIDs base64 to 43 chars on their own, plus a
    22-char signature and the `UG1..` framing is already a 70-char floor
    before any CBOR structure. The real token is 97 chars, which fits QR
    version 6 at ECC M (106-byte capacity) — one version above the estimate,
    same error correction, still a comfortable scan in bad light.

    What the CBOR choice is actually buying is pinned here: the same payload
    as JSON is 123 bytes against 52, which would push the token past 190
    chars and two QR versions higher.
    """
    assert len(_mint()) <= 106

    payload = _mint().split(".")[1]
    assert len(urlsafe_b64decode(payload + "==")) <= 60


def test_a_tampered_payload_is_refused_as_an_invalid_signature(settings):
    prefix, payload, signature = _mint(serial=1).split(".")
    raw = cbor2.loads(urlsafe_b64decode(payload + "=="))
    raw["s"] = 2  # re-serial it: the forger's whole goal
    forged = urlsafe_b64encode(cbor2.dumps(raw)).rstrip(b"=").decode()

    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(f"{prefix}.{forged}.{signature}", expected_event_id=EVENT)
    assert exc.value.code is ErrorCode.INVALID_SIGNATURE


def test_a_tampered_signature_is_refused(settings):
    prefix, payload, signature = _mint().split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]

    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(f"{prefix}.{payload}.{flipped}", expected_event_id=EVENT)
    assert exc.value.code is ErrorCode.INVALID_SIGNATURE


def test_a_token_for_another_event_is_refused_with_its_own_code(settings):
    """Distinct from INVALID_SIGNATURE on purpose: a genuine pass for last
    month's event is a confused attendee, not a forgery attempt."""
    other_event = uuid4()
    token = _mint(event_id=other_event)

    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(token, expected_event_id=EVENT)
    assert exc.value.code is ErrorCode.TOKEN_WRONG_EVENT


def test_an_expired_token_is_refused(settings):
    token = _mint(expires_at=datetime.now(UTC) + timedelta(seconds=30))

    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(
            token,
            expected_event_id=EVENT,
            now=datetime.now(UTC) + timedelta(seconds=31),
        )
    assert exc.value.code is ErrorCode.PASS_EXPIRED


def test_expiry_is_exclusive_at_the_boundary(settings):
    expires_at = _future()
    token = _mint(expires_at=expires_at)
    at_expiry = expires_at.replace(microsecond=0)

    verify_pass_token(token, expected_event_id=EVENT, now=at_expiry - timedelta(seconds=1))
    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(token, expected_event_id=EVENT, now=at_expiry)
    assert exc.value.code is ErrorCode.PASS_EXPIRED


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "UG1.only-two-parts",
        "UG2.aaaa.bbbb",
        "UG1.!!!not-base64!!!.bbbb",
        "UG1.YWJj.bbbb",  # valid base64, not CBOR map
    ],
)
def test_malformed_tokens_are_refused_as_invalid_token(settings, token):
    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(token, expected_event_id=EVENT)
    assert exc.value.code is ErrorCode.INVALID_TOKEN


def test_rotating_the_signing_key_kills_every_issued_token(monkeypatch, settings):
    from app.core.settings import get_settings

    token = _mint()
    monkeypatch.setenv("PASS_SIGNING_KEY", "a-completely-different-key")
    get_settings.cache_clear()

    with pytest.raises(PassTokenError) as exc:
        verify_pass_token(token, expected_event_id=EVENT)
    assert exc.value.code is ErrorCode.INVALID_SIGNATURE


def test_serial_must_fit_the_uint16_field(settings):
    with pytest.raises(ValueError, match="uint16"):
        _mint(serial=70_000)


def test_fingerprint_is_stable_and_is_not_the_token(settings):
    token = _mint()
    assert token_fingerprint(token) == token_fingerprint(token)
    assert token not in token_fingerprint(token)
    assert len(token_fingerprint(token)) == 64
