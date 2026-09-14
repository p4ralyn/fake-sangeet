from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, ENUM
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, UUIDPk


class StaffRole(StrEnum):
    """Phase 0 decision 2.1 — replaces the five-value v1 enum.

    PARTICIPANT and VISITOR are derived at request time and never stored.
    """

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"


staff_role_enum = ENUM(StaffRole, name="staff_role", create_type=False)


class User(UUIDPk, Base):
    """Anyone who ever authenticated. `ms_object_id` is the real identity key."""

    __tablename__ = "users"

    ms_object_id: Mapped[str | None] = mapped_column(Text, unique=True)
    net_id: Mapped[str | None] = mapped_column(CITEXT, index=True)
    email: Mapped[str | None] = mapped_column(CITEXT)
    display_name: Mapped[str | None] = mapped_column(Text)
    auth_method: Mapped[str] = mapped_column(Text, nullable=False, server_default="MICROSOFT_SSO")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class EventStaff(UUIDPk, EventScoped, Base):
    """Seeded by hand (5-15 rows). Pre-provisioned by net_id before first login."""

    __tablename__ = "event_staff"
    __table_args__ = (
        UniqueConstraint("event_id", "net_id", "role", name="event_staff_event_netid_role_key"),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    net_id: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[StaffRole] = mapped_column(staff_role_enum, nullable=False)
    gate_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    added_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
