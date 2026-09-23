"""ID upload and human review (M4). OCR is cut — a person looks at the photo.

The funnel, end to end:

    ticket PURCHASED                      ingested from the sheet (M3)
        │ person signs in and uploads their ID card
        ▼
    ticket MANUAL_REVIEW                  verification OPEN, waiting on a human
        │
        ├─ reviewer approves  → ticket VERIFIED → pass issued → PASS_ISSUED
        ├─ reviewer asks again → ticket RETRY_REQUIRED   (attempts left)
        └─ reviewer rejects    → ticket REJECTED         (no attempts left,
                                                          or a deliberate no)

Two rules that are not negotiable and are enforced here rather than remembered:

* **Nobody uploads for somebody else.** Every write is scoped to the ticket the
  authenticated NetID actually holds. There is no registration id in any
  request body — it is derived, so it cannot be tampered with.
* **A rejection is never silent.** Every decision writes an audit row naming
  the reviewer, and the retry budget is spent in the same transaction that
  records the decision.

The bytes go to a private bucket through the storage seam. Nothing here ever
returns an object key to a participant, and the only way anyone sees an image
is a signed URL minted at the moment they are entitled to look.
"""
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models import (
    TICKET_LIVE_STATUSES,
    Event,
    Order,
    Pass,
    Ticket,
    TicketStatus,
    User,
)
from app.models.verification import (
    VERIFY_OPEN_OUTCOMES,
    IdImage,
    Registration,
    Verification,
    VerifyOutcome,
)
from app.schemas.verification import MyVerification, ReviewItem, ReviewQueue
from app.services.audit import write_audit, write_funnel_event
from app.services.passes.issue import issue_pass
from app.services.storage.provider import ObjectStore
from app.services.verification.images import InspectedImage, inspect

#: `fraud_flags` vocabulary. One entry, because SHA-256 equality is the only
#: fraud signal that survives OCR being cut. Never auto-rejects — §9.3's rule
#: that accusing is worse than queuing holds: it is a note for the reviewer.
FLAG_DUPLICATE_IMAGE = "DUPLICATE_IMAGE"

#: Ticket statuses from which an upload is meaningful. Derived from the live
#: set rather than hand-listed, minus the two that are already done with the
#: ID step — CLAUDE.md: never hardcode a "known values" set.
UPLOADABLE_TICKET_STATUSES: frozenset[TicketStatus] = TICKET_LIVE_STATUSES - {
    TicketStatus.VERIFIED,
    TicketStatus.PASS_ISSUED,
}


class NoTicket(Exception):
    """This NetID holds no live ticket for this event."""


class UploadNotAllowed(Exception):
    """A real reason the person may not upload right now, in their words."""


class AlreadyDecided(Exception):
    """Two reviewers opened the same row; the second one loses."""


def object_key_for(event_id: UUID, registration_id: UUID, content_type: str) -> str:
    """`evt/{event}/reg/{registration}/{uuid}.{ext}` per LLD-v1 §9.2.

    A fresh uuid per upload, never a name derived from the person: bucket keys
    leak into logs and lifecycle reports, and a key should not say whose ID it
    is.
    """
    extension = "png" if content_type.endswith("png") else "jpg"
    return f"evt/{event_id}/reg/{registration_id}/{uuid4()}.{extension}"


async def ticket_for_user(db: AsyncSession, *, event_id: UUID, user: User) -> Ticket:
    """The ticket this identity holds, binding it on first sight.

    A sheet-ingested ticket carries `bound_net_id` and no `user_id` — nobody had
    signed in when it was minted. The first time they do, this is where the two
    halves meet.

    Prefers a live ticket, but falls back to a dead one. That fallback is the
    whole reason this is two queries: a person whose ID was refused still has a
    REJECTED ticket, and they must be told they were refused. Filtering to live
    rows only would answer "we can't find a ticket for your account", which
    reads as a broken website at exactly the moment somebody needs a straight
    answer.
    """
    mine = (Ticket.user_id == user.id) | (Ticket.bound_net_id == user.net_id)
    found = await db.scalar(
        select(Ticket)
        .where(
            Ticket.event_id == event_id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
            mine,
        )
        .order_by(Ticket.seq_in_order)
        .limit(1)
    )
    if found is None:
        found = await db.scalar(
            select(Ticket)
            .where(Ticket.event_id == event_id, mine)
            .order_by(Ticket.created_at.desc())
            .limit(1)
        )
    if found is None:
        raise NoTicket(f"no ticket for {user.net_id}")
    if found.user_id is None and found.status in TICKET_LIVE_STATUSES:
        found.user_id = user.id
        found.bound_at = found.bound_at or datetime.now(UTC)
        await db.flush()
    return found


async def get_registration(
    db: AsyncSession, *, ticket_id: UUID
) -> Registration | None:
    """The CURRENT registration. A transferred ticket keeps the previous
    holder's as history, and history must never answer "who holds this"."""
    return await db.scalar(
        select(Registration).where(
            Registration.ticket_id == ticket_id, Registration.superseded_at.is_(None)
        )
    )


async def ensure_registration(
    db: AsyncSession, *, event: Event, user: User, ticket: Ticket, phone: str | None = None
) -> Registration:
    """One registration per ticket, created at the first upload.

    Identity comes from the session, never from the request body: a person may
    correct their phone number, not whose ID this is.
    """
    existing = await get_registration(db, ticket_id=ticket.id)
    if existing is not None:
        if phone and not existing.phone:
            existing.phone = phone
        return existing

    # The best human name available, in that order: what the identity provider
    # knows them as, then what the organiser typed into the sheet, and only
    # then the NetID. A volunteer matching a face to a card needs a name, and
    # "Pk944" is not one.
    sheet_name = await db.scalar(
        select(Order.purchaser_name).where(Order.id == ticket.order_id)
    )
    registration = Registration(
        event_id=event.id,
        ticket_id=ticket.id,
        user_id=user.id,
        full_name=user.display_name or sheet_name or user.net_id,
        net_id=user.net_id,
        email=user.email or f"{user.net_id}@{get_settings().google_allowed_domain}",
        phone=phone,
        consent_version=get_settings().id_consent_version,
        consented_at=datetime.now(UTC),
    )
    db.add(registration)
    await db.flush()
    return registration


async def latest_verification(
    db: AsyncSession, *, registration_id: UUID
) -> Verification | None:
    return await db.scalar(
        select(Verification)
        .where(Verification.registration_id == registration_id)
        .order_by(Verification.attempt_no.desc())
        .limit(1)
    )


def attempts_remaining(registration: Registration) -> int:
    return max(0, get_settings().max_id_upload_attempts - registration.attempts)


async def assert_can_upload(
    db: AsyncSession, *, ticket: Ticket, registration: Registration | None
) -> None:
    """Every reason an upload is refused, checked in one place so the route and
    the status endpoint can never disagree about whether the button is live."""
    if ticket.status not in UPLOADABLE_TICKET_STATUSES:
        if ticket.status in (TicketStatus.VERIFIED, TicketStatus.PASS_ISSUED):
            raise UploadNotAllowed("Your ID is already verified.")
        if ticket.status is TicketStatus.REJECTED:
            raise UploadNotAllowed(
                "Your ID check was refused. Talk to the desk if you think that’s wrong."
            )
        raise UploadNotAllowed("This ticket can’t accept an ID photo.")
    if registration is None:
        return
    if attempts_remaining(registration) <= 0:
        raise UploadNotAllowed(
            "You’ve used all your upload attempts. Contact the desk and they’ll sort it out."
        )
    current = await latest_verification(db, registration_id=registration.id)
    if current is not None and current.outcome in VERIFY_OPEN_OUTCOMES:
        raise UploadNotAllowed("Your ID photo is already waiting to be checked.")


async def submit_id_image(
    db: AsyncSession,
    store: ObjectStore,
    *,
    event: Event,
    user: User,
    raw: bytes,
    consented: bool,
    phone: str | None = None,
) -> Verification:
    """Store one ID photo and open a review. Raises before writing anything if
    the upload is not allowed.

    Consent is checked here and the schema makes it structural: there is no
    path that writes a `registrations` row without a version and a timestamp.
    """
    if not consented:
        raise UploadNotAllowed(
            "Tick the consent box so we can use your ID photo to check you in."
        )

    ticket = await ticket_for_user(db, event_id=event.id, user=user)
    registration = await get_registration(db, ticket_id=ticket.id)
    await assert_can_upload(db, ticket=ticket, registration=registration)

    # Sanitise before anything is persisted: an unreadable or oversized file
    # should cost a message, not a bucket object and a spent attempt.
    image = inspect(raw)

    registration = await ensure_registration(
        db, event=event, user=user, ticket=ticket, phone=phone
    )
    flags = await _fraud_flags(db, event_id=event.id, image=image)

    key = object_key_for(event.id, registration.id, image.content_type)
    await store.put(key, image.data, image.content_type)

    stored = IdImage(
        event_id=event.id,
        registration_id=registration.id,
        object_key=key,
        content_type=image.content_type,
        bytes=image.bytes_len,
        sha256=image.sha256,
        width=image.width,
        height=image.height,
    )
    db.add(stored)
    await db.flush()

    registration.attempts += 1
    verification = Verification(
        event_id=event.id,
        registration_id=registration.id,
        id_image_id=stored.id,
        attempt_no=registration.attempts,
        outcome=VerifyOutcome.MANUAL_REVIEW,
        fraud_flags=flags,
    )
    db.add(verification)

    ticket.status = TicketStatus.MANUAL_REVIEW
    await db.flush()

    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=user.id,
        actor_net_id=user.net_id,
        actor_role=None,
        action="ID_IMAGE_UPLOADED",
        object_type="verification",
        object_id=verification.id,
        # No object key, no filename, no dimensions worth arguing about — an
        # audit row is not a place to describe somebody's photograph.
        after={"attempt_no": verification.attempt_no, "flags": flags},
    )
    await write_funnel_event(db, event_id=event.id, user_id=user.id, step="ID_UPLOADED")
    return verification


async def _fraud_flags(
    db: AsyncSession, *, event_id: UUID, image: InspectedImage
) -> list[str]:
    """The same file, uploaded twice. A note for the reviewer, never a refusal
    — two siblings photographing one card on one phone is a support call, not
    an accusation."""
    twin = await db.scalar(
        select(IdImage.id)
        .where(IdImage.event_id == event_id, IdImage.sha256 == image.sha256)
        .limit(1)
    )
    return [FLAG_DUPLICATE_IMAGE] if twin is not None else []


async def review_verification(
    db: AsyncSession,
    *,
    event: Event,
    verification_id: UUID,
    reviewer: User,
    reviewer_role: str | None,
    approve: bool,
    note: str | None = None,
    allow_resubmit: bool = True,
) -> Verification:
    """Apply a reviewer's decision. Idempotent by refusal: a row that already
    carries a decision raises rather than being decided twice.

    Approving issues the pass. That is the whole point of the milestone — a
    verified person has something to scan at the door.
    """
    verification = await db.scalar(
        select(Verification).where(
            Verification.id == verification_id, Verification.event_id == event.id
        )
    )
    if verification is None:
        raise LookupError(f"no verification {verification_id} for this event")
    if verification.outcome not in VERIFY_OPEN_OUTCOMES:
        raise AlreadyDecided(f"already {verification.outcome.value}")

    registration = await db.get(Registration, verification.registration_id)
    if registration is None:  # pragma: no cover -- FK ON DELETE CASCADE
        raise LookupError("verification has no registration")
    ticket = await db.get(Ticket, registration.ticket_id)
    if ticket is None:  # pragma: no cover -- FK ON DELETE CASCADE
        raise LookupError("registration has no ticket")

    before = {"ticket_status": ticket.status.value, "outcome": verification.outcome.value}

    if approve:
        verification.outcome = VerifyOutcome.APPROVED
        ticket.status = TicketStatus.VERIFIED
    elif allow_resubmit and attempts_remaining(registration) > 0:
        verification.outcome = VerifyOutcome.RETRY_REQUIRED
        ticket.status = TicketStatus.RETRY_REQUIRED
    else:
        # Out of attempts, or a deliberate final no. Either way the person is
        # told, and the seat is NOT released here — an admin decides that
        # separately, because a rejected ID is not always an absent guest.
        verification.outcome = VerifyOutcome.REJECTED
        ticket.status = TicketStatus.REJECTED

    verification.reviewed_by = reviewer.id
    verification.reviewed_at = datetime.now(UTC)
    verification.review_note = note
    if not approve and note:
        verification.failure_reasons = [note]
    await db.flush()

    issued: Pass | None = None
    if approve:
        issued = await issue_pass(db, event=event, ticket=ticket)
        issued.registration_id = registration.id
        ticket.status = TicketStatus.PASS_ISSUED
        await db.flush()

    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=reviewer.id,
        actor_net_id=reviewer.net_id,
        actor_role=reviewer_role,
        action="ID_REVIEWED",
        object_type="verification",
        object_id=verification.id,
        before=before,
        after={
            "outcome": verification.outcome.value,
            "ticket_status": ticket.status.value,
            "pass_issued": issued is not None,
        },
    )
    return verification


async def pending_reviews(
    db: AsyncSession, *, event_id: UUID, limit: int = 50, offset: int = 0
) -> list[tuple[Verification, Registration]]:
    """The review queue, oldest first — a person who uploaded an hour ago has
    been waiting an hour."""
    rows = await db.execute(
        select(Verification, Registration)
        .join(Registration, Registration.id == Verification.registration_id)
        .where(
            Verification.event_id == event_id,
            Verification.outcome.in_(VERIFY_OPEN_OUTCOMES),
        )
        .order_by(Verification.created_at, Verification.id)
        .limit(limit)
        .offset(offset)
    )
    return [(verification, registration) for verification, registration in rows.all()]


async def pending_count(db: AsyncSession, *, event_id: UUID) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(Verification)
        .where(
            Verification.event_id == event_id,
            Verification.outcome.in_(VERIFY_OPEN_OUTCOMES),
        )
    ) or 0


async def signed_image_url(
    db: AsyncSession, store: ObjectStore, *, id_image_id: UUID
) -> str | None:
    """A short-lived URL for one ID photo, or None once the bytes are purged.

    The only channel through which anyone — a reviewer or a gate volunteer —
    ever sees one of these images.
    """
    image = await db.get(IdImage, id_image_id)
    if image is None or image.purged_at is not None:
        return None
    return await store.signed_url(
        image.object_key, expires_seconds=get_settings().id_image_url_ttl_seconds
    )


# ── views ───────────────────────────────────────────────────────────────────
# Response assembly lives here, not in the route. The route validates,
# authorizes and calls; these build the two shapes it returns. Pydantic crosses
# the boundary, FastAPI never crosses into here.


async def my_verification_view(
    db: AsyncSession, *, event: Event, user: User
) -> MyVerification:
    """What a person is told about their own ID check.

    `can_upload` is computed from the same `assert_can_upload` the upload route
    enforces with, so the button the screen shows and the answer the server
    gives can never disagree.
    """
    ticket = await ticket_for_user(db, event_id=event.id, user=user)
    registration = await get_registration(db, ticket_id=ticket.id)
    latest = (
        await latest_verification(db, registration_id=registration.id)
        if registration is not None
        else None
    )

    blocked: str | None = None
    try:
        await assert_can_upload(db, ticket=ticket, registration=registration)
    except UploadNotAllowed as exc:
        blocked = str(exc)

    # A reviewer's note reaches the participant only where it is actionable or
    # owed: "take another photo", or "you were refused, and here is why".
    decided = latest is not None and latest.outcome not in VERIFY_OPEN_OUTCOMES
    return MyVerification(
        ticket_status=ticket.status,
        outcome=latest.outcome if latest else None,
        attempts_used=registration.attempts if registration else 0,
        attempts_remaining=(
            attempts_remaining(registration)
            if registration
            else get_settings().max_id_upload_attempts
        ),
        can_upload=blocked is None,
        blocked_reason=blocked,
        review_note=latest.review_note if (decided and latest and latest.review_note) else None,
        submitted_at=latest.created_at if latest else None,
        reviewed_at=latest.reviewed_at if latest else None,
        consent_version=registration.consent_version if registration else None,
    )


async def review_item_view(
    db: AsyncSession,
    store: ObjectStore,
    *,
    verification: Verification,
    registration: Registration | None = None,
) -> ReviewItem:
    """One queue row, with a freshly signed image URL.

    The URL is minted per response and lives 90 seconds, so a queue left open
    on a laptop stops being a window into anybody's ID.
    """
    registration = registration or await db.get(Registration, verification.registration_id)
    if registration is None:  # pragma: no cover -- FK ON DELETE CASCADE
        raise LookupError("verification has no registration")
    ticket = await db.get(Ticket, registration.ticket_id)
    if ticket is None:  # pragma: no cover -- FK ON DELETE CASCADE
        raise LookupError("registration has no ticket")
    return ReviewItem(
        verification_id=verification.id,
        registration_id=registration.id,
        attempt_no=verification.attempt_no,
        outcome=verification.outcome,
        full_name=registration.full_name,
        net_id=registration.net_id,
        email=registration.email,
        ticket_status=ticket.status,
        fraud_flags=list(verification.fraud_flags),
        submitted_at=verification.created_at,
        id_image_url=await signed_image_url(db, store, id_image_id=verification.id_image_id),
    )


async def review_queue_view(
    db: AsyncSession, store: ObjectStore, *, event_id: UUID, limit: int, offset: int
) -> ReviewQueue:
    rows = await pending_reviews(db, event_id=event_id, limit=limit, offset=offset)
    return ReviewQueue(
        pending=await pending_count(db, event_id=event_id),
        items=[
            await review_item_view(db, store, verification=verification, registration=registration)
            for verification, registration in rows
        ],
    )
