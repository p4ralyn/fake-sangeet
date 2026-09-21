from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
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


class LedgerReason(StrEnum):
    """Why seats moved (addendum §A1). INGEST is M3's addition: cohort A's
    already-sold rows are the first seats this system ever commits."""

    INGEST = "INGEST"
    HOLD = "HOLD"
    EXPIRE = "EXPIRE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    ADMIN_RELEASE = "ADMIN_RELEASE"
    FORCE_APPROVE = "FORCE_APPROVE"


# Rendered from the enum, never hand-typed into DDL.
_LEDGER_REASON_SQL = ", ".join(f"'{reason.value}'" for reason in sorted(LedgerReason))
LEDGER_REASON_CHECK = "reason IN (" + _LEDGER_REASON_SQL + ")"


class InventoryLedger(Base):
    """APPEND ONLY (addendum §A1). Every seat movement, so a drifting
    `seats_committed` can be reconstructed and audited.

    CLAUDE.md makes this non-negotiable, and M3 is where the first seat actually
    moves — the M1 plan deferred the table to M5 on the assumption that nothing
    before it would commit a seat. Sheet ingestion does.

    `intent_id` has no FK: `payment_intents` is M5. Same deferral as
    `passes.registration_id`. It gets its constraint when the table lands.
    """

    __tablename__ = "inventory_ledger"
    __table_args__ = (
        CheckConstraint(LEDGER_REASON_CHECK, name="inventory_ledger_reason_known"),
        CheckConstraint("delta <> 0", name="inventory_ledger_delta_non_zero"),
        CheckConstraint(
            "seats_committed_after >= 0", name="inventory_ledger_after_non_negative"
        ),
        Index("ix_inventory_ledger_event_created", "event_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    intent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    seats_committed_after: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
