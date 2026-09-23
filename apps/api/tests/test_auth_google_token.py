"""Google ID token validation, against a token shaped like a real one.

This exists because of a bug that made Google sign-in fail 100% of the time
while looking intermittent: the validator required an `nbf` claim, which
Microsoft issues and Google does not. Every attempt died with
`MissingRequiredClaimError` and surfaced as "We could not verify that sign-in.
Try again."

The tests mint a token with a locally generated RSA key and hand the public key
to the JWKS cache, so the whole verification path runs for real — signature,
issuer, audience, expiry, required claims, `hd`, `email_verified`, nonce. A
test that stubbed `jwt.decode` would have passed happily while production
rejected everybody.

The payload below is deliberately built from Google's advertised
`claims_supported` and nothing else. If a future change requires a claim Google
does not send, these fail.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.errors import AppError
from app.services.identity.google import REQUIRED_CLAIMS, GoogleAuthProvider

KID = "test-key"
NONCE = "nonce-value"

# Exactly what https://accounts.google.com/.well-known/openid-configuration
# advertises. Notably absent: `nbf`.
GOOGLE_CLAIMS = frozenset(
    {
        "aud", "email", "email_verified", "exp", "family_name", "given_name",
        "iat", "iss", "name", "picture", "sub",
    }
)


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def google(monkeypatch, keypair, settings):
    """A provider wired to our own signing key and a fixed client id."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAIN", "snu.edu.in")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    provider = GoogleAuthProvider()
    _, public = keypair

    async def signing_key(kid: str):
        return public

    monkeypatch.setattr(provider._jwks, "signing_key", signing_key)
    yield provider
    get_settings.cache_clear()


def google_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id",
        "sub": "1234567890",
        "hd": "snu.edu.in",
        "email": "zq205@snu.edu.in",
        "email_verified": True,
        "name": "Kabir Mehra",
        "given_name": "Kabir",
        "family_name": "Mehra",
        "picture": "https://lh3.googleusercontent.com/a/abc",
        "iat": now,
        "exp": now + 3600,
        "nonce": NONCE,
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


def sign(keypair, payload: dict) -> str:
    private, _ = keypair
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": KID})


async def exchange(google, keypair, payload: dict):
    token = sign(keypair, payload)

    async def _exchange_code(code, verifier):
        return token

    google._exchange_code = _exchange_code
    return await google.exchange(
        params={"code": "auth-code"}, stored={"verifier": "v", "nonce": NONCE}
    )


# ── the bug ──────────────────────────────────────────────────────────────────
def test_nbf_is_not_required_because_google_never_sends_it():
    """The regression, stated as plainly as it can be. Requiring `nbf` here
    rejected every real sign-in."""
    assert "nbf" not in REQUIRED_CLAIMS


def test_every_required_claim_is_one_google_actually_issues():
    """Catches the whole class, not just `nbf`: requiring anything Google does
    not send makes sign-in fail for everybody, always."""
    assert set(REQUIRED_CLAIMS) <= GOOGLE_CLAIMS


async def test_a_real_shaped_google_token_is_accepted(google, keypair):
    """The payload carries no `nbf` — exactly like production."""
    payload = google_payload()
    assert "nbf" not in payload

    claims = await exchange(google, keypair, payload)

    assert claims.net_id == "zq205"
    assert claims.email == "zq205@snu.edu.in"
    assert claims.display_name == "Kabir Mehra"
    assert claims.avatar_url == "https://lh3.googleusercontent.com/a/abc"
    assert claims.auth_method == "GOOGLE_OAUTH"
    assert claims.subject == "1234567890"


# ── the checks that must keep working ────────────────────────────────────────
async def test_a_personal_gmail_account_is_refused(google, keypair):
    """`hd` on the authorize call is only a UI hint; this claim check inside
    the verified token is the actual enforcement."""
    with pytest.raises(AppError) as refused:
        await exchange(google, keypair, google_payload(hd=None, email="someone@gmail.com"))
    assert "not from this university" in refused.value.message


async def test_a_token_for_another_client_is_refused(google, keypair):
    with pytest.raises(AppError):
        await exchange(google, keypair, google_payload(aud="someone-elses-client"))


async def test_an_expired_token_is_refused(google, keypair):
    now = int(time.time())
    with pytest.raises(AppError):
        await exchange(google, keypair, google_payload(iat=now - 7200, exp=now - 3600))


async def test_a_replayed_nonce_is_refused(google, keypair):
    """Without this a captured token could be replayed into a fresh sign-in."""
    with pytest.raises(AppError):
        await exchange(google, keypair, google_payload(nonce="a-different-nonce"))


async def test_an_unverified_email_is_refused(google, keypair):
    with pytest.raises(AppError):
        await exchange(google, keypair, google_payload(email_verified=False))


async def test_a_token_signed_by_somebody_else_is_refused(google, monkeypatch):
    """The signature check is the foundation everything else rests on."""
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(google_payload(), attacker, algorithm="RS256", headers={"kid": KID})

    async def _exchange_code(code, verifier):
        return token

    google._exchange_code = _exchange_code
    with pytest.raises(AppError):
        await google.exchange(
            params={"code": "c"}, stored={"verifier": "v", "nonce": NONCE}
        )


async def test_a_token_with_no_name_falls_back_to_the_netid(google, keypair):
    """`display_name` is shown to a volunteer at a door; it can never be blank."""
    claims = await exchange(google, keypair, google_payload(name=None))
    assert claims.display_name == "zq205"


async def test_a_token_with_no_picture_is_fine(google, keypair):
    claims = await exchange(google, keypair, google_payload(picture=None))
    assert claims.avatar_url is None
