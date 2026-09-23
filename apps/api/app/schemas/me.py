from uuid import UUID

from pydantic import BaseModel

from app.services.identity.roles import Role


class EventSummary(BaseModel):
    id: UUID
    slug: str
    name: str


class MeResponse(BaseModel):
    """Phase 0 decision 2.4. Single event, so no membership list."""

    user_id: UUID
    net_id: str
    display_name: str
    email: str | None
    #: Null for anyone who has not signed in with a provider that supplies one.
    avatar_url: str | None = None
    roles: list[Role]
    event: EventSummary
    csrf_token: str
