import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT = "22222222-2222-2222-2222-222222222222.apps.googleusercontent.com"
DOMAIN = "snu.edu.in"
KID = "test-key-1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_hs256_confusion_token(rsa_key, **overrides) -> str:
    """RS256/HS256 confusion attack: a hand-crafted JWT whose header claims
    HS256, HMAC-signed using Google's own RSA *public* key bytes as the shared
    secret -- something an attacker can always obtain. Built by hand (not
    `jwt.encode`) because PyJWT's encoder itself refuses to sign with an
    asymmetric key as an HMAC secret; a hand-crafted token has no such guard,
    so this is what the wire format actually lets an attacker send.
    """
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "115792089237316195423",
        "email": "aarav.sharma@snu.edu.in",
        "email_verified": True,
        "hd": DOMAIN,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    public_pem = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode()}.{_b64url(signature)}"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def google_settings(monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setenv("AUTH_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shhh")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAIN", DOMAIN)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def make_id_token(rsa_key, **overrides):
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "115792089237316195423",
        "email": "Aarav.Sharma@snu.edu.in",
        "email_verified": True,
        "hd": DOMAIN,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def patched_provider(monkeypatch, rsa_key, google_settings):
    """Stub the two network calls: JWKS fetch and the token exchange."""
    from app.services.identity import google as google_module

    async def fake_signing_key(self, kid: str):
        assert kid == KID
        return rsa_key.public_key()

    monkeypatch.setattr(google_module.JwksCache, "signing_key", fake_signing_key)
    return google_module


async def test_authorization_url_carries_hd_and_pkce(patched_provider):
    provider = patched_provider.GoogleAuthProvider()
    stored = await provider.state_payload()
    url = await provider.authorization_url(state="abc", next_url="/me", stored=stored)

    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "code_challenge_method=S256" in url
    assert "state=abc" in url
    assert "verifier" in stored and "nonce" in stored

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["client_id"] == [CLIENT]
    assert query["hd"] == [DOMAIN]

    # The challenge must actually be base64url(sha256(verifier)) with padding
    # stripped -- not just present. Otherwise PKCE is theater.
    expected_digest = hashlib.sha256(stored["verifier"].encode("ascii")).digest()
    expected_challenge = _b64url(expected_digest)
    assert query["code_challenge"] == [expected_challenge]


async def test_valid_token_yields_claims(monkeypatch, patched_provider, rsa_key):
    provider = patched_provider.GoogleAuthProvider()
    token = make_id_token(rsa_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    claims = await provider.exchange(
        params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
    )

    assert claims.subject == "115792089237316195423"
    assert claims.net_id == "aarav.sharma"
    assert claims.email == "aarav.sharma@snu.edu.in"
    assert claims.display_name == "Aarav Sharma"
    assert claims.auth_method == "GOOGLE_OAUTH"


async def test_alternate_issuer_form_is_accepted(monkeypatch, patched_provider, rsa_key):
    """Google genuinely issues tokens with either issuer spelling."""
    provider = patched_provider.GoogleAuthProvider()
    token = make_id_token(rsa_key, iss="accounts.google.com")

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    claims = await provider.exchange(
        params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
    )
    assert claims.subject == "115792089237316195423"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "someone-else"}, "audience"),
        ({"iss": "https://evil.example"}, "issuer"),
        # exp must be pushed past the mandated 120s clock-skew allowance --
        # anything closer than that is correctly accepted (CLAUDE.md/spec:
        # "exp/nbf with a 120-second clock skew allowance"). exp=now-10 sits
        # INSIDE the leeway and can never fail against a correct implementation.
        ({"exp": int(time.time()) - 300, "iat": int(time.time()) - 3600,
          "nbf": int(time.time()) - 3600}, "expiry"),
        ({"nonce": "wrong-nonce"}, "nonce"),
        ({"hd": "gmail.com"}, "hd wrong domain"),
        ({"email_verified": False}, "email not verified"),
    ],
)
async def test_invalid_tokens_are_rejected(
    monkeypatch, patched_provider, rsa_key, overrides, reason
):
    from app.core.errors import AppError

    provider = patched_provider.GoogleAuthProvider()
    token = make_id_token(rsa_key, **overrides)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID", reason


async def test_hd_claim_missing_entirely_is_rejected(monkeypatch, patched_provider, rsa_key):
    """A personal Gmail account simply has no `hd` claim at all -- distinct
    from `hd` being present with the wrong value, and from Google's own
    default `hd=None` behavior in a naive `.get()` check (which would let a
    dict-shaped `{"hd": None}` slip past a `!=` comparison the wrong way if
    the default target were also `None`)."""
    from app.core.errors import AppError

    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "115792089237316195423",
        "email": "aarav.sharma@gmail.com",
        "email_verified": True,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})
    provider = patched_provider.GoogleAuthProvider()

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


async def test_token_missing_sub_is_rejected_not_a_500(monkeypatch, patched_provider, rsa_key):
    """A token that is genuinely, correctly signed by Google but omits `sub`
    must still fail closed as AppError(OIDC_TOKEN_INVALID)/401 -- not escape
    as a raw KeyError that the generic exception handler turns into a 500."""
    from app.core.errors import AppError

    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "email": "aarav.sharma@snu.edu.in",
        "email_verified": True,
        "hd": DOMAIN,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})
    provider = patched_provider.GoogleAuthProvider()

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


async def test_token_missing_email_is_rejected(monkeypatch, patched_provider, rsa_key):
    """sub present but email absent must also fail closed rather than
    yielding a claims object with a blank identity / empty net_id."""
    from app.core.errors import AppError

    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "115792089237316195423",
        "email_verified": True,
        "hd": DOMAIN,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})
    provider = patched_provider.GoogleAuthProvider()

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


async def test_token_signed_by_the_wrong_key_is_rejected(monkeypatch, patched_provider):
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    from app.core.errors import AppError

    attacker_key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    provider = patched_provider.GoogleAuthProvider()
    token = make_id_token(attacker_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError):
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )


async def test_missing_code_is_rejected(patched_provider):
    from app.core.errors import AppError

    provider = patched_provider.GoogleAuthProvider()
    with pytest.raises(AppError):
        await provider.exchange(params={}, stored={"verifier": "v", "nonce": "n"})


# ── algorithm-confusion regression tests ───────────────────────────────────
# `algorithms=["RS256"]` is what makes both of these safe. These tests exist
# so a future edit that loosens the allowlist (e.g. `algorithms=["RS256",
# "HS256"]`, or a client-supplied `alg`) fails loudly instead of silently.


async def test_alg_none_token_is_rejected(monkeypatch, patched_provider):
    from app.core.errors import AppError

    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "115792089237316195423",
        "email": "aarav.sharma@snu.edu.in",
        "email_verified": True,
        "hd": DOMAIN,
        "name": "Aarav Sharma",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, key="", algorithm="none", headers={"kid": KID})

    provider = patched_provider.GoogleAuthProvider()

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


async def test_hs256_signed_with_public_key_as_secret_is_rejected(
    monkeypatch, patched_provider, rsa_key
):
    """The classic RS256->HS256 downgrade: an attacker who only ever sees
    Google's *public* signing key HMAC-signs their own token with it, betting
    the verifier will accept any `alg` the token claims. Our pinned
    `algorithms=["RS256"]` must refuse this outright."""
    from app.core.errors import AppError

    token = make_hs256_confusion_token(rsa_key)
    provider = patched_provider.GoogleAuthProvider()

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.GoogleAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


# ── `_exchange_code`'s real body, offline via httpx.MockTransport ──────────
# Every test above stubs `_exchange_code` itself, so the actual POST -- the
# other network call this task exists to build -- was otherwise never
# executed. `httpx.MockTransport` drives it end to end with no real socket.


def _mock_http_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_code_posts_pkce_verifier_and_returns_id_token(
    monkeypatch, patched_provider, google_settings
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"id_token": "the-id-token"})

    client = _mock_http_client(handler)
    monkeypatch.setattr(patched_provider, "get_http_client", lambda: client)

    provider = patched_provider.GoogleAuthProvider()
    try:
        token = await provider._exchange_code("authcode", "the-verifier")
    finally:
        await client.aclose()

    assert token == "the-id-token"
    body = urllib.parse.parse_qs(captured["request"].content.decode())
    assert body["code_verifier"] == ["the-verifier"]
    assert body["grant_type"] == ["authorization_code"]
    assert body["client_id"] == [CLIENT]
    assert body["code"] == ["authcode"]


async def test_exchange_code_non_200_response_is_rejected(
    monkeypatch, patched_provider, google_settings
):
    from app.core.errors import AppError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = _mock_http_client(handler)
    monkeypatch.setattr(patched_provider, "get_http_client", lambda: client)

    provider = patched_provider.GoogleAuthProvider()
    try:
        with pytest.raises(AppError) as excinfo:
            await provider._exchange_code("authcode", "the-verifier")
    finally:
        await client.aclose()
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"


async def test_exchange_code_response_without_id_token_is_rejected(
    monkeypatch, patched_provider, google_settings
):
    from app.core.errors import AppError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "not-what-we-asked-for"})

    client = _mock_http_client(handler)
    monkeypatch.setattr(patched_provider, "get_http_client", lambda: client)

    provider = patched_provider.GoogleAuthProvider()
    try:
        with pytest.raises(AppError) as excinfo:
            await provider._exchange_code("authcode", "the-verifier")
    finally:
        await client.aclose()
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID"
