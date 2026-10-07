"""Queue mail into `email_outbox`. Sending is M6.

The queue, not the sender, is where double-sends are prevented: `idempotency_key`
is UNIQUE and every insert is `ON CONFLICT DO NOTHING`. Re-running the ingester
after the client fixes four rows therefore queues four new emails, not 295.
"""
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.models import EmailOutbox, EmailStatus

#: Template names the outbox knows about. A worker resolves these to bodies in
#: M6; queueing an unknown one now would dead-letter silently then.
TEMPLATE_TICKET_READY = "ticket_ready"
#: "Your ID was checked": approved, send a clearer photo, or not accepted.
TEMPLATE_ID_REVIEWED = "id_reviewed"
#: "Someone sent you a pass": a transfer, or a couple's partner seat.
TEMPLATE_PASS_RECEIVED = "pass_received"  # noqa: S105 -- a template name, not a password


class QueuedEmail(BaseModel):
    """Pydantic at the boundary: nothing hands this module a bare dict."""

    to_email: EmailStr
    template: str
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    registration_id: UUID | None = None


async def queue_email(db: AsyncSession, *, event_id: UUID, message: QueuedEmail) -> bool:
    """Insert one queued message. Returns True if this call created the row.

    False means an identical message was already queued — the normal, correct
    outcome of a second ingest run, not an error.
    """
    statement = (
        insert(EmailOutbox)
        .values(
            id=uuid7(),
            event_id=event_id,
            registration_id=message.registration_id,
            to_email=str(message.to_email),
            template=message.template,
            payload=message.payload,
            idempotency_key=message.idempotency_key,
            status=EmailStatus.QUEUED.value,
        )
        .on_conflict_do_nothing(index_elements=[EmailOutbox.idempotency_key])
        .returning(EmailOutbox.id)
    )
    created = await db.execute(statement)
    return created.scalar_one_or_none() is not None
