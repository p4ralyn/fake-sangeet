from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, Timestamps, UUIDPk


class GateType(StrEnum):
    ENTRY_ONLY = "ENTRY_ONLY"
    EXIT_ONLY = "EXIT_ONLY"
    BOTH = "BOTH"


gate_type_enum = ENUM(GateType, name="gate_type", create_type=False)


class Event(UUIDPk, Timestamps, Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    slug: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Kolkata")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registration_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    # Shape is addendum §A1: capacity, ticket_types, hold_ttl_minutes, hold_grace_minutes.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Gate(UUIDPk, EventScoped, Base):
    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("event_id", "name", name="gates_event_name_key"),)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[GateType] = mapped_column(gate_type_enum, nullable=False, server_default="BOTH")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class EventInventory(Base):
    """Addendum §A1: seats, not orders. ONE row per event, mutated under FOR UPDATE.

    `no_oversell` is the last line of defence — it holds even when application
    logic is wrong. Nothing may write these columns outside the inventory service.
    """

    __tablename__ = "event_inventory"
    __table_args__ = (
        CheckConstraint("seats_total > 0", name="seats_total_positive"),
        CheckConstraint("seats_committed >= 0", name="seats_committed_non_negative"),
        CheckConstraint("seats_committed <= seats_total", name="no_oversell"),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    seats_total: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_committed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
