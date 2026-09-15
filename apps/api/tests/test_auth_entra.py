import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
KID = "test-key-1"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def entra_settings(monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "shhh")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def make_id_token(rsa_key, **overrides):
    now = int(time.time())
    payload = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": "aaaabbbb-cccc-dddd-eeee-ffff00001111",
        "preferred_username": "Aarav.Sharma@university.edu",
        "name": "Aarav Sharma",
        "email": "aarav.sharma@university.edu",
        "nonce": "test-nonce",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def patched_provider(monkeypatch, rsa_key, entra_settings):
    """Stub the two network calls: JWKS fetch and the token exchange."""
    from app.services.identity import entra as entra_module

    async def fake_signing_key(self, kid: str):
        assert kid == KID
        return rsa_key.public_key()

    monkeypatch.setattr(entra_module.JwksCache, "signing_key", fake_signing_key)
    return entra_module


async def test_authorization_url_carries_pkce_and_tenant(patched_provider):
    provider = patched_provider.EntraAuthProvider()
    stored = await provider.state_payload()
    url = await provider.authorization_url(state="abc", next_url="/me", stored=stored)

    assert f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    assert "state=abc" in url
    assert f"client_id={CLIENT}" in url
    assert "verifier" in stored and "nonce" in stored


async def test_valid_token_yields_claims(monkeypatch, patched_provider, rsa_key):
    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(rsa_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    claims = await provider.exchange(
        params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
    )

    assert claims.subject == "aaaabbbb-cccc-dddd-eeee-ffff00001111"
    assert claims.net_id == "aarav.sharma@university.edu"
    assert claims.display_name == "Aarav Sharma"
    assert claims.auth_method == "MICROSOFT_SSO"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "someone-else"}, "audience"),
        ({"tid": "99999999-9999-9999-9999-999999999999"}, "tenant"),
        ({"iss": "https://evil.example/v2.0"}, "issuer"),
        # exp must be pushed past the mandated 120s clock-skew allowance --
        # anything closer than that is correctly accepted (CLAUDE.md/spec:
        # "exp/nbf with a 120-second clock skew allowance").
        ({"exp": int(time.time()) - 300, "iat": int(time.time()) - 3600,
          "nbf": int(time.time()) - 3600}, "expiry"),
        ({"nonce": "wrong-nonce"}, "nonce"),
    ],
)
async def test_invalid_tokens_are_rejected(
    monkeypatch, patched_provider, rsa_key, overrides, reason
):
    from app.core.errors import AppError

    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(rsa_key, **overrides)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError) as excinfo:
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )
    assert excinfo.value.code.value == "OIDC_TOKEN_INVALID", reason


async def test_token_signed_by_the_wrong_key_is_rejected(monkeypatch, patched_provider):
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    from app.core.errors import AppError

    attacker_key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    provider = patched_provider.EntraAuthProvider()
    token = make_id_token(attacker_key)

    async def fake_exchange_code(self, code, verifier):
        return token

    monkeypatch.setattr(patched_provider.EntraAuthProvider, "_exchange_code", fake_exchange_code)

    with pytest.raises(AppError):
        await provider.exchange(
            params={"code": "authcode"}, stored={"verifier": "v", "nonce": "test-nonce"}
        )


async def test_missing_code_is_rejected(patched_provider):
    from app.core.errors import AppError

    provider = patched_provider.EntraAuthProvider()
    with pytest.raises(AppError):
        await provider.exchange(params={}, stored={"verifier": "v", "nonce": "n"})
