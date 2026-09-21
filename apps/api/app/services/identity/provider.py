"""The auth seam.

Three implementations, chosen by `AUTH_PROVIDER`:
  dev    — impersonate any NetID. Local only; `Settings` refuses it when APP_ENV=prod.
  entra  — real Microsoft Entra OIDC (Authorization Code + PKCE).
  google — real Google Workspace OIDC (Authorization Code + PKCE), restricted to
           the `snu.edu.in` domain via the verified ID token's `hd` claim.

A fourth (email OTP for non-university attendees, LLD-v2 §15 item 5) drops in here
without touching a route.
"""
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.settings import AuthProviderName, get_settings


class IdentityClaims(BaseModel):
    """What every provider must produce. Nothing provider-specific leaks past this."""

    subject: str          # immutable identity key (Entra `oid`, or `dev:<net_id>`)
    net_id: str           # lowercased, trimmed — for display and matching
    email: str | None
    display_name: str
    auth_method: str


@runtime_checkable
class AuthProvider(Protocol):
    name: str

    async def state_payload(self) -> dict:
        """Provider-specific values to stash in Redis alongside the state (PKCE, nonce)."""
        ...

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        ...

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        ...


def get_auth_provider() -> AuthProvider:
    """Derived from the settings enum, not a hardcoded if/else chain."""
    from app.services.identity.dev import DevAuthProvider
    from app.services.identity.entra import EntraAuthProvider
    from app.services.identity.google import GoogleAuthProvider

    registry: dict[AuthProviderName, type[AuthProvider]] = {
        AuthProviderName.DEV: DevAuthProvider,
        AuthProviderName.ENTRA: EntraAuthProvider,
        AuthProviderName.GOOGLE: GoogleAuthProvider,
    }
    missing = set(AuthProviderName) - set(registry)
    if missing:
        raise RuntimeError(f"AuthProvider registry is missing implementations for {missing}")
    return registry[get_settings().auth_provider]()
