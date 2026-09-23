"""Registrations, ID images and their review (LLD-v1 §9, §12).

Three tables:

* `registrations` — the person behind a ticket, as they described themselves.
  One row per ticket. LLD-v1 §12 also gives it the checkout fields (department,
  year, emergency contact, dietary notes); those stay nullable and unwritten
  until M5's details form exists, because cohort A never fills a form — they
  sign in and upload an ID.
* `id_images`    — one row per uploaded card. Private bucket, signed URLs only,
  purged at T+45 days (CLAUDE.md guardrail). The bytes never live in Postgres.
* `verifications`— one row per *attempt*. The retry loop is counted here, and
  `attempt_no` is unique per registration so two concurrent uploads cannot both
  claim attempt 3.

**OCR is cut** (CLAUDE.md Part 3). §12's `engine`, `ocr_raw`, `extracted`,
`subscores`, `score` and `latency_ms` are therefore absent rather than present
and forever NULL: a column that nothing can ever write is a standing invitation
to write something into it. `fraud_flags` survives, because the one fraud signal
that costs nothing without OCR — the same file uploaded twice, caught by
SHA-256 — is still worth recording.

What replaces the scoring model is a person looking at the photo. Every image
therefore lands in `MANUAL_REVIEW`, which is exactly where §9.3 sent anything
it was unsure about anyway.
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
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, Timestamps, UUIDPk


class VerifyOutcome(StrEnum):
    """`verifications.outcome` (LLD-v1 §9.3's decision column, minus the
    automatic verdicts).

    With OCR cut, nothing reaches `AUTO_PASS` — the constant is deliberately
    NOT defined, so no code can route to a state no reviewer produced.
    """

    MANUAL_REVIEW = "MANUAL_REVIEW"
    APPROVED = "APPROVED"
    RETRY_REQUIRED = "RETRY_REQUIRED"
    REJECTED = "REJECTED"


#: Outcomes a reviewer has already acted on. Derived, never hand-listed, so the
#: admin queue's filter and any later report cannot drift from the enum.
VERIFY_DECIDED_OUTCOMES: frozenset[VerifyOutcome] = frozenset(
    {VerifyOutcome.APPROVED, VerifyOutcome.RETRY_REQUIRED, VerifyOutcome.REJECTED}
)
VERIFY_OPEN_OUTCOMES: frozenset[VerifyOutcome] = (
    frozenset(VerifyOutcome) - VERIFY_DECIDED_OUTCOMES
)

verify_outcome_enum = ENUM(VerifyOutcome, name="verify_outcome", create_type=False)


class Registration(UUIDPk, EventScoped, Timestamps, Base):
    """One per ticket. Created the moment somebody first uploads an ID.

    `consent_version` / `consented_at` are NOT NULL because there is no lawful
    moment at which we hold a photograph of someone's student ID without having
    asked. The upload endpoint refuses without consent; the schema makes that
    refusal impossible to bypass.
    """

    __tablename__ = "registrations"
    __table_args__ = (
        # One LIVE registration per ticket, not one ever. A transferred ticket
        # keeps the previous holder's registration -- and their verification
        # history -- as a superseded row, because deleting it would destroy the
        # record that somebody's ID was checked. The partial index is what lets
        # both facts be true at once.
        Index(
            "registrations_ticket_key",
            "ticket_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        CheckConstraint("attempts >= 0", name="registrations_attempts_non_negative"),
        Index("ix_registrations_event_net_id", "event_id", "net_id"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    net_id: Mapped[str] = mapped_column(CITEXT, nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    # M5's details form fills these. Nullable and unwritten at M4.
    department: Mapped[str | None] = mapped_column(Text)
    year_of_study: Mapped[str | None] = mapped_column(Text)
    emergency_contact: Mapped[str | None] = mapped_column(Text)
    dietary_notes: Mapped[str | None] = mapped_column(Text)
    consent_version: Mapped[str] = mapped_column(Text, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: How many images this person has uploaded. The retry budget counts down
    #: from `Settings.max_id_upload_attempts`.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    email_deliverable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Set when the ticket moved to somebody else. A superseded registration is
    #: history: it is never the answer to "who holds this ticket", and the
    #: partial unique index above stops it blocking the new holder's row.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdImage(UUIDPk, EventScoped, Base):
    """One uploaded card.

    `object_key` is a key in the private bucket, never a URL: a URL implies a
    host, and the only host that may ever serve these bytes is a signed,
    short-lived one minted at the moment somebody is entitled to look.

    Named `object_key` rather than §12's `gcs_object` because the storage seam
    has two backends and neither is named in the column elsewhere.
    """

    __tablename__ = "id_images"
    __table_args__ = (
        CheckConstraint("bytes > 0", name="id_images_bytes_positive"),
        Index("ix_id_images_registration", "registration_id"),
        # Same file uploaded twice is the one fraud signal that survives OCR
        # being cut, so the lookup that finds it gets an index.
        Index("ix_id_images_event_sha256", "event_id", "sha256"),
    )

    registration_id: Mapped[UUID] = mapped_column(
        ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Set by the T+45 purge. A row with `purged_at` still proves the check
    #: happened after the bytes are gone — which is the point of keeping it.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Verification(UUIDPk, EventScoped, Base):
    """One review attempt. `UNIQUE (registration_id, attempt_no)` is the guard
    that makes the retry budget real under concurrency."""

    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "attempt_no", name="verifications_registration_attempt_key"
        ),
        CheckConstraint("attempt_no >= 1", name="verifications_attempt_positive"),
        # A decided row has a decider and a time; an open row has neither.
        # Stated as a constraint because "reviewed_by is set when reviewed_at
        # is" is exactly the kind of pairing that rots in application code.
        CheckConstraint(
            "(reviewed_at IS NULL) = (reviewed_by IS NULL)",
            name="verifications_review_pair",
        ),
        Index("ix_verifications_event_outcome", "event_id", "outcome"),
        Index("ix_verifications_registration", "registration_id"),
    )

    registration_id: Mapped[UUID] = mapped_column(
        ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False
    )
    id_image_id: Mapped[UUID] = mapped_column(
        ForeignKey("id_images.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[VerifyOutcome] = mapped_column(verify_outcome_enum, nullable=False)
    fraud_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    failure_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
