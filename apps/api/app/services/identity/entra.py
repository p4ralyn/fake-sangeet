"""Microsoft Entra ID — OIDC Authorization Code + PKCE (LLD-v1 §8.1).

We never keep Microsoft access or refresh tokens and make no Graph calls at
runtime. The ID token is validated once, then our own session takes over.
"""
import base64
import hashlib
import time
from collections.abc import Mapping
from urllib.parse import urlencode

import httpx
import jwt
import structlog

from app.core.errors import AppError, ErrorCode
from app.core.security import new_token
from app.core.settings import get_settings
from app.services.identity.provider import IdentityClaims

log = structlog.get_logger()

JWKS_TTL_SECONDS = 43_200  # 12h
CLOCK_SKEW_SECONDS = 120
HTTP_401 = 401


class JwksCache:
    """Caches Microsoft's signing keys; refreshes on a `kid` miss."""

    def __init__(self) -> None:
        self._keys: dict[str, object] = {}
        self._fetched_at: float = 0.0

    @property
    def _url(self) -> str:
        return (
            f"https://login.microsoftonline.com/"
            f"{get_settings().entra_tenant_id}/discovery/v2.0/keys"
        )

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            document = response.json()
        self._keys = {
            key["kid"]: jwt.PyJWK(key).key for key in document.get("keys", []) if "kid" in key
        }
        self._fetched_at = time.monotonic()

    async def signing_key(self, kid: str):
        stale = (time.monotonic() - self._fetched_at) > JWKS_TTL_SECONDS
        if kid not in self._keys or stale:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            )
        return key


_jwks = JwksCache()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class EntraAuthProvider:
    name = "entra"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._jwks = _jwks

    @property
    def _authority(self) -> str:
        return f"https://login.microsoftonline.com/{self._settings.entra_tenant_id}"

    async def state_payload(self) -> dict:
        return {"verifier": new_token(48), "nonce": new_token(16)}

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        settings = self._settings
        query = {
            "client_id": settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "response_mode": "query",
            "scope": "openid profile email",
            "state": state,
            "nonce": stored["nonce"],
            "code_challenge": _pkce_challenge(stored["verifier"]),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if settings.allowed_domains:
            query["domain_hint"] = settings.allowed_domains[0]
        return f"{self._authority}/oauth2/v2.0/authorize?{urlencode(query)}"

    async def _exchange_code(self, code: str, verifier: str) -> str:
        settings = self._settings
        payload = {
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "code_verifier": verifier,
            "scope": "openid profile email",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self._authority}/oauth2/v2.0/token", data=payload)
        if response.status_code != 200:
            log.warning("entra_token_exchange_failed", status_code=response.status_code)
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That sign-in could not be completed. Try again.",
                http_status=HTTP_401,
            )
        id_token = response.json().get("id_token")
        if not id_token:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That sign-in could not be completed. Try again.",
                http_status=HTTP_401,
            )
        return id_token

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        code = params.get("code")
        if not code:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                params.get("error_description") or "Sign-in was cancelled.",
                http_status=HTTP_401,
            )

        id_token = await self._exchange_code(code, stored["verifier"])
        settings = self._settings

        try:
            header = jwt.get_unverified_header(id_token)
            key = await self._jwks.signing_key(header["kid"])
            payload = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=settings.entra_client_id,
                issuer=f"{self._authority}/v2.0",
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "nbf", "iat", "aud", "iss"]},
            )
        except AppError:
            raise
        except Exception as exc:
            log.warning("entra_id_token_invalid", error_type=type(exc).__name__)
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            ) from exc

        if payload.get("tid") != settings.entra_tenant_id:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That account is not from this university's tenant.",
                http_status=HTTP_401,
            )
        if payload.get("nonce") != stored.get("nonce"):
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            )

        net_id = (
            payload.get("preferred_username") or payload.get("upn") or payload.get("email") or ""
        ).strip().lower()
        return IdentityClaims(
            subject=payload["oid"],
            net_id=net_id,
            email=(payload.get("email") or net_id) or None,
            display_name=payload.get("name") or net_id,
            auth_method="MICROSOFT_SSO",
        )
