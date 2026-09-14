"""Placeholder for Microsoft Entra OIDC. Task 7's deliverable.

This file exists only so `app.services.identity.provider.get_auth_provider`'s
registry is complete and its `AuthProviderName` completeness check stays
honest — the check is the point of the seam. Task 7 replaces this module in
full with the real Authorization Code + PKCE flow.
"""
from collections.abc import Mapping

from app.services.identity.provider import IdentityClaims


class EntraAuthProvider:
    name = "entra"

    async def state_payload(self) -> dict:
        raise NotImplementedError("EntraAuthProvider lands in M1 Task 7")

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        raise NotImplementedError("EntraAuthProvider lands in M1 Task 7")

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        raise NotImplementedError("EntraAuthProvider lands in M1 Task 7")
