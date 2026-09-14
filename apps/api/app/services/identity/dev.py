"""Impersonate any NetID without an Azure app registration.

Never reachable in production: `Settings._enforce_environment_guards` raises at
import time if APP_ENV=prod and AUTH_PROVIDER=dev, and the picker route 404s
whenever the active provider is not this one.
"""
from collections.abc import Mapping

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.services.identity.provider import IdentityClaims


class DevAuthProvider:
    name = "dev"

    async def state_payload(self) -> dict:
        return {}

    async def authorization_url(self, *, state: str, next_url: str, stored: dict) -> str:
        return f"/auth/dev/picker?state={state}"

    async def exchange(self, *, params: Mapping[str, str], stored: dict) -> IdentityClaims:
        net_id = (params.get("net_id") or "").strip().lower()
        if not net_id:
            raise AppError(ErrorCode.UNKNOWN_NET_ID, "Pick a NetID to sign in as.")
        domains = get_settings().allowed_domains
        domain = domains[0] if domains else "localhost"
        return IdentityClaims(
            subject=f"dev:{net_id}",
            net_id=net_id,
            email=f"{net_id}@{domain}",
            display_name=" ".join(part.capitalize() for part in net_id.replace(".", " ").split()),
            auth_method="DEV",
        )
