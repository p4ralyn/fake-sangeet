"""Google Workspace OIDC — Authorization Code + PKCE.

Sign-in is restricted to the `snu.edu.in` Google Workspace domain. Google's
`hd=` authorize parameter is only a UI hint an attacker can trivially skip
(sign in with a personal Gmail account instead) -- the only thing that
actually enforces the restriction is verifying the `hd` claim inside the
signed, verified ID token, which is what `exchange()` below does. This is
the exact analogue of the Entra provider's `tid` tenant check.

We never keep Google access or refresh tokens and make no further Google API
calls at runtime. The ID token is validated once, then our own session
takes over.
"""
import base64
import hashlib
import time
from collections.abc import Mapping
from urllib.parse import urlencode

import jwt
import structlog

from app.core.errors import AppError, ErrorCode
from app.core.http import get_http_client
from app.core.security import new_token
from app.core.settings import get_settings
from app.services.identity.provider import IdentityClaims

log = structlog.get_logger()

JWKS_TTL_SECONDS = 43_200  # 12h
CLOCK_SKEW_SECONDS = 120
HTTP_401 = 401

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- URL, not a secret
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

# Google genuinely issues ID tokens with either form of the issuer claim.
# Pinning only one causes intermittent, spurious rejections.
VALID_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# Claims a Google ID token must carry, and it is a SHORT list on purpose.
#
# There is deliberately no `nbf` here. Google does not issue one -- its
# discovery document advertises exactly
# `aud, email, email_verified, exp, family_name, given_name, iat, iss, name,
# picture, sub` -- so requiring it rejects every real sign-in with
# `MissingRequiredClaimError`, which surfaces as a generic "we could not verify
# that sign-in" and looks like an intermittent fault rather than a total one.
# This module was adapted from `entra.py`, where `nbf` IS correct because
# Microsoft issues it; the claim came along with the copy. Do not add it back.
REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss", "sub"]


class JwksCache:
    """Caches Google's signing keys; refreshes on a `kid` miss."""

    def __init__(self) -> None:
        self._keys: dict[str, object] = {}
        self._fetched_at: float = 0.0

    async def _refresh(self) -> None:
        client = get_http_client()
        response = await client.get(JWKS_URL, timeout=10)
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


class GoogleAuthProvider:
    name = "google"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._jwks = _jwks

    async def state_payload(self) -> dict:
        return {"verifier": new_token(48), "nonce": new_token(16)}

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        settings = self._settings
        query = {
            "client_id": settings.google_client_id,
            "response_type": "code",
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "scope": "openid email profile",
            "state": state,
            "nonce": stored["nonce"],
            "code_challenge": _pkce_challenge(stored["verifier"]),
            "code_challenge_method": "S256",
            "hd": settings.google_allowed_domain,
            "prompt": "select_account",
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    async def _exchange_code(self, code: str, verifier: str) -> str:
        settings = self._settings
        payload = {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
            "code_verifier": verifier,
        }
        client = get_http_client()
        response = await client.post(TOKEN_URL, data=payload, timeout=15)
        if response.status_code != 200:
            log.warning("google_token_exchange_failed", status_code=response.status_code)
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
                audience=settings.google_client_id,
                issuer=list(VALID_ISSUERS),
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except AppError:
            raise
        except Exception as exc:
            # `claim` names WHICH claim was missing. Without it this log says
            # only "MissingRequiredClaimError" and the next person has to go
            # and read Google's discovery document to find out which -- which
            # is exactly what happened the first time.
            log.warning(
                "google_id_token_invalid",
                error_type=type(exc).__name__,
                claim=getattr(exc, "claim", None),
            )
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            ) from exc

        # `hd` on the authorize call is only a UI hint -- an attacker can skip
        # it entirely and sign in with a personal Gmail account whose token is
        # still validly signed by Google. This claim check inside the verified
        # token is the actual enforcement.
        # Compared case-insensitively: a `GOOGLE_ALLOWED_DOMAIN=SNU.edu.in` in
        # .env would otherwise reject every real account, which fails closed but
        # is baffling to diagnose.
        claimed_domain = str(payload.get("hd") or "").strip().lower()
        if claimed_domain != settings.google_allowed_domain.strip().lower():
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That account is not from this university's domain.",
                http_status=HTTP_401,
            )
        if payload.get("email_verified") is not True:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "That account's email is not verified.",
                http_status=HTTP_401,
            )
        if payload.get("nonce") != stored.get("nonce"):
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            )

        sub = payload.get("sub")
        email = (payload.get("email") or "").strip().lower()
        net_id = email.rsplit("@", 1)[0] if "@" in email else ""
        if not sub or not net_id or not email:
            raise AppError(
                ErrorCode.OIDC_TOKEN_INVALID,
                "We could not verify that sign-in. Try again.",
                http_status=HTTP_401,
            )
        # Everything the `profile` scope gives us that this app has a use for.
        # `name` is why the scope is requested at all: without it every screen
        # falls back to a NetID, and "Pk944" is not a name to show a volunteer
        # matching a face at a door. `picture` is the one other claim worth
        # keeping. `given_name`/`family_name`/`locale` are deliberately dropped
        # — nothing here reads them, and an unused column holding somebody's
        # personal data is a liability, not a feature.
        return IdentityClaims(
            subject=sub,
            net_id=net_id,
            email=email,
            display_name=payload.get("name") or net_id,
            avatar_url=payload.get("picture") or None,
            auth_method="GOOGLE_OAUTH",
        )
