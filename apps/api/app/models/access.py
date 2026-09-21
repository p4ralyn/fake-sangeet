"""Passes and scans — the gate (LLD-v1 §11, §12).

`scans` is append-only and the database enforces it: migration 0002 installs a
trigger that raises on UPDATE and on DELETE. That is deliberate — a convention
that only lives in a docstring is not an invariant.

Deferred by scope, not forgotten:
  * `passes.registration_id` lands with `registrations` in M4.
  * `scans.device_id` gets its FK when `scanner_devices` lands in M6; until then
    it records whatever the scanner reported, unvalidated.
"""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, UUIDPk


class PassState(StrEnum):
    NOT_ARRIVED = "NOT_ARRIVED"
    INSIDE = "INSIDE"
    ON_BREAK = "ON_BREAK"
    DEPARTED = "DEPARTED"
    REVOKED = "REVOKED"


class ScanAction(StrEnum):
    CHECK_IN = "CHECK_IN"
    EXIT_BREAK = "EXIT_BREAK"
    RE_ENTRY = "RE_ENTRY"
    CHECK_OUT = "CHECK_OUT"
    DENIED = "DENIED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class ScanResult(StrEnum):
    """`scans.result`. Text in the schema (§12), constrained to these values."""

    OK = "OK"
    DENIED = "DENIED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    OVERRIDE = "OVERRIDE"


# Terminal states accept no scan at all. Derived here so the state machine and
# any future admin tooling read the same list.
TERMINAL_PASS_STATES: frozenset[PassState] = frozenset(
    {PassState.DEPARTED, PassState.REVOKED}
)

# Rendered from the enum, never hand-typed into DDL.
_SCAN_RESULT_SQL = ", ".join(f"'{result.value}'" for result in ScanResult)
SCAN_RESULT_CHECK = "result IN (" + _SCAN_RESULT_SQL + ")"

pass_state_enum = ENUM(PassState, name="pass_state", create_type=False)
scan_action_enum = ENUM(ScanAction, name="scan_action", create_type=False)


class Pass(UUIDPk, EventScoped, Base):
    """The scannable artefact. `serial` is what kills a reissued or revoked pass
    without changing `pass_id` (LLD-v1 §10.1); `version` is the optimistic guard
    on top of the row lock (§11.3)."""

    __tablename__ = "passes"
    __table_args__ = (
        UniqueConstraint("event_id", "short_code", name="passes_event_short_code_key"),
        CheckConstraint("serial >= 1", name="passes_serial_positive"),
        CheckConstraint("breaks_used >= 0", name="passes_breaks_used_non_negative"),
        CheckConstraint("version >= 0", name="passes_version_non_negative"),
        Index("ix_passes_event_state", "event_id", "current_state"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    serial: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    short_code: Mapped[str] = mapped_column(Text, nullable=False)
    qr_object: Mapped[str | None] = mapped_column(Text)
    current_state: Mapped[PassState] = mapped_column(
        pass_state_enum, nullable=False, server_default="NOT_ARRIVED"
    )
    breaks_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_checkin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Scan(UUIDPk, EventScoped, Base):
    """APPEND ONLY — enforced by `scans_append_only()` in migration 0002.

    `pass_id` is nullable on purpose: a forged or unparseable token has no pass,
    and that attempt is exactly the one worth keeping for forensics.
    """

    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("pass_id", "client_scan_uuid", name="scans_pass_client_uuid_key"),
        CheckConstraint(SCAN_RESULT_CHECK, name="scans_result_known"),
        Index("ix_scans_event_occurred", "event_id", text("occurred_at DESC")),
        Index("ix_scans_pass_occurred", "pass_id", "occurred_at"),
        Index("ix_scans_event_gate_occurred", "event_id", "gate_id", text("occurred_at DESC")),
    )

    pass_id: Mapped[UUID | None] = mapped_column(ForeignKey("passes.id"))
    gate_id: Mapped[UUID | None] = mapped_column(ForeignKey("gates.id"))
    scanned_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    device_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[ScanAction] = mapped_column(scan_action_enum, nullable=False)
    from_state: Mapped[PassState | None] = mapped_column(pass_state_enum)
    to_state: Mapped[PassState | None] = mapped_column(pass_state_enum)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    deny_reason: Mapped[str | None] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)
    offline: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    client_scan_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # sha256 of the presented token. NEVER the token itself: it is a bearer
    # credential, and this table is read by more people than Redis is.
    raw_token_hash: Mapped[str | None] = mapped_column(Text)
