"""Wire shapes for ID upload and review (M4).

Two response models for the queue, not one filtered model — the same rule
`schemas/scans.py` follows for SECURITY. A participant asking about their own
verification gets `MyVerification`, which carries no reviewer identity, no
fraud flags and no object key. A reviewer gets `ReviewItem`, which does. The
narrow model is a separate class so a field added to the rich one can never
appear on the participant's response by accident.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.ticketing import TicketStatus
from app.models.verification import VerifyOutcome


class MyVerification(BaseModel):
    """What the person who uploaded is told about their own ID check."""

    ticket_status: TicketStatus
    outcome: VerifyOutcome | None = None
    attempts_used: int = 0
    attempts_remaining: int = 0
    can_upload: bool
    #: Present when `can_upload` is false, in words the person can act on.
    blocked_reason: str | None = None
    #: The reviewer's note, only when they asked for another photo or refused.
    #: This is the one piece of reviewer output a participant ever sees.
    review_note: str | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    consent_version: str | None = None


class ReviewItem(BaseModel):
    """One row of the reviewer's queue. Carries PII by design — this endpoint
    is ADMIN/OWNER only and every read of the image URL is short-lived."""

    verification_id: UUID
    registration_id: UUID
    attempt_no: int
    outcome: VerifyOutcome
    full_name: str
    net_id: str
    email: str
    ticket_status: TicketStatus
    fraud_flags: list[str] = Field(default_factory=list)
    submitted_at: datetime
    #: A signed URL with a short TTL, or null once the bytes are purged.
    #: Never cached by the client, never stored, never emailed.
    id_image_url: str | None = None


class ReviewQueue(BaseModel):
    pending: int
    items: list[ReviewItem]


class ReviewDecision(BaseModel):
    """`allow_resubmit` is only consulted on a refusal. Defaulting it to true
    makes the merciful path the one you get by forgetting — a reviewer who
    means "no, never" has to say so."""

    approve: bool
    note: str | None = Field(default=None, max_length=500)
    allow_resubmit: bool = True
