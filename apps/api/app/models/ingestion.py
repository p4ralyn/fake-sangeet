"""Ingestion bookkeeping and the mail outbox (LLD-v1 §12).

Three tables, one job each:

* `ticket_sources`   — where a batch of orders came from. LLD-v2 §6 collapses
  this to ONE row for the dry run rather than dropping it, so `orders.source_id`
  and `ingestion_errors.source_id` keep pointing at something real and the
  multi-source seam survives into v1.
* `ingestion_errors` — the "needs attention" tray. A defective row lands here
  instead of aborting the batch (LLD-v1 §7.2); 295 rows with 4 bad ones mint 291
  tickets and write 4 of these.
* `email_outbox`     — queued mail. `idempotency_key` is UNIQUE, which is what
  makes re-running the ingester safe: the second run's INSERT conflicts and
  nobody gets a second "your ticket is ready".

Sending is M6. M3 only queues.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, UUIDPk


class EmailStatus(StrEnum):
    """`email_outbox.status` (LLD-v1 §12). Text in the schema, constrained to
    these values — the worker that claims rows is M6, but the vocabulary it will
    claim against is fixed now so the CHECK and the worker cannot drift."""

    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    DEAD = "DEAD"


# States a row will never be picked up from again. Derived, not hand-listed, so
# the M6 worker's claim query and any admin tray read the same set.
EMAIL_TERMINAL_STATUSES: frozenset[EmailStatus] = frozenset(
    {EmailStatus.SENT, EmailStatus.DEAD}
)
EMAIL_CLAIMABLE_STATUSES: frozenset[EmailStatus] = (
    frozenset(EmailStatus) - EMAIL_TERMINAL_STATUSES
)

# Rendered from the enum, never hand-typed into DDL.
_EMAIL_STATUS_SQL = ", ".join(f"'{status.value}'" for status in sorted(EmailStatus))
EMAIL_STATUS_CHECK = "status IN (" + _EMAIL_STATUS_SQL + ")"


class TicketSource(UUIDPk, EventScoped, Base):
    """Where orders came from. One row per logical source, so a re-ingest of the
    Early Bird workbook and a re-ingest of the Phase 1 form never share a
    watermark."""

    __tablename__ = "ticket_sources"
    __table_args__ = (
        Index(
            "ticket_sources_event_key",
            "event_id",
            "source_key",
            unique=True,
        ),
    )

    # LLD-v1 §7.1 vocabulary: google_sheet|payment_webhook|csv_upload|manual.
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: The adapter's stable logical name (EARLY_BIRD, PHASE1). Not a filename.
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_watermark: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class IngestionError(UUIDPk, EventScoped, Base):
    """One defective or noteworthy source row. Never a reason to fail a batch.

    `raw` holds the normaliser's view of the row. It DOES contain attendee data —
    that is the whole point, an admin has to read it to fix the sheet — so it is
    never logged and the admin route that will surface it (M6) is ADMIN-only.
    """

    __tablename__ = "ingestion_errors"
    __table_args__ = (
        # The same open problem on the same row exists once, however many times
        # the ingester runs. Enforced here rather than by the service remembering
        # to de-duplicate: a re-run is the normal case, not the exception.
        Index(
            "ingestion_errors_open_row_key",
            "event_id",
            "source_id",
            "row_ref",
            "reason",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_ingestion_errors_event_unresolved", "event_id", "resolved_at"),
    )

    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_sources.id", ondelete="SET NULL")
    )
    #: Human-addressable position, e.g. "EARLY_BIRD/INFERNO:37".
    row_ref: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmailOutbox(UUIDPk, EventScoped, Base):
    """Queued mail. `idempotency_key` UNIQUE is the anti-double-send (LLD-v1 §9).

    `registration_id` is specified in §12 but `registrations` is M4, so the
    column exists without its FK — the same deferral `passes.registration_id`
    carries. It gets the constraint when the table lands.
    """

    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint(EMAIL_STATUS_CHECK, name="email_outbox_status_known"),
        CheckConstraint("attempts >= 0", name="email_outbox_attempts_non_negative"),
        Index("ix_email_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    registration_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    to_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
