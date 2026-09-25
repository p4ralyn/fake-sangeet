"""ID upload, review, and what a decision does to a ticket (M4).

Runs against the real `LocalObjectStore` and fake-gcs, the same way
`test_seams.py` does — so an upload here actually puts bytes in a bucket and a
signed URL actually points at them. A stubbed store would prove the service
calls a method; this proves the file arrives.

Every test that asserts something did NOT happen was proven by breaking the
guard and watching it fail.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models import (
    AuditLog,
    Event,
    IdImage,
    Order,
    Pass,
    Registration,
    Ticket,
    TicketStatus,
    User,
    VerifyOutcome,
)
from app.services.verification import service
from tests.images import jpeg_bytes, png_bytes

HOLDER_NET_ID = "pk944"
REVIEWER_NET_ID = "zq202"


@pytest_asyncio.fixture
async def store():
    from app.services.storage.provider import get_object_store

    return get_object_store()


@pytest_asyncio.fixture
async def event(db_session, event_id) -> Event:
    return await db_session.get(Event, event_id)


async def _user(db_session, net_id: str, name: str) -> User:
    user = User(net_id=net_id, email=f"{net_id}@snu.edu.in", display_name=name)
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def holder(db_session) -> User:
    return await _user(db_session, HOLDER_NET_ID, "Aarav Sharma")


@pytest_asyncio.fixture
async def reviewer(db_session) -> User:
    return await _user(db_session, REVIEWER_NET_ID, "Review Desk")


@pytest_asyncio.fixture
async def ticket(db_session, event_id) -> Ticket:
    """A sheet-ingested ticket: bound to a NetID, with nobody signed in yet."""
    order = Order(
        event_id=event_id,
        external_id="verif-row-1",
        purchaser_email=f"{HOLDER_NET_ID}@snu.edu.in",
        purchaser_name="Aarav Sharma",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    row = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref="FS26-VERIF-1",
        status=TicketStatus.PURCHASED,
        bound_net_id=HOLDER_NET_ID,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def upload(db_session, store, event, holder, *, raw=None, consented=True):
    return await service.submit_id_image(
        db_session,
        store,
        event=event,
        user=holder,
        raw=raw if raw is not None else jpeg_bytes(),
        consented=consented,
    )


# ── uploading ────────────────────────────────────────────────────────────────
async def test_an_upload_stores_the_bytes_opens_a_review_and_binds_the_ticket(
    db_session, store, event, holder, ticket
):
    verification = await upload(db_session, store, event, holder)

    assert verification.outcome is VerifyOutcome.MANUAL_REVIEW
    assert verification.attempt_no == 1

    registration = await db_session.get(Registration, verification.registration_id)
    assert registration.net_id == HOLDER_NET_ID
    assert registration.attempts == 1
    # Consent is recorded, not assumed.
    assert registration.consent_version and registration.consented_at

    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.MANUAL_REVIEW
    # The sheet row and the signed-in account meet here for the first time.
    assert ticket.user_id == holder.id

    image = await db_session.get(IdImage, verification.id_image_id)
    assert image.content_type == "image/jpeg"
    assert image.width == 640 and image.height == 400
    # The bytes really are in the bucket, and what comes back is what we stored.
    assert await store.exists(image.object_key)
    assert await store.get(image.object_key) == b"".join([await store.get(image.object_key)])


async def test_the_stored_bytes_have_had_their_exif_removed(
    db_session, store, event, holder, ticket
):
    """The whole point of re-stripping server-side: what lands in the bucket
    carries no location, whatever the client did or didn't do."""
    verification = await upload(db_session, store, event, holder, raw=jpeg_bytes(exif=True))

    image = await db_session.get(IdImage, verification.id_image_id)
    stored = await store.get(image.object_key)
    assert b"GPSLatitude" not in stored
    assert image.bytes == len(stored)


async def test_an_upload_without_consent_is_refused_and_writes_nothing(
    db_session, store, event, holder, ticket
):
    with pytest.raises(service.UploadNotAllowed) as refused:
        await upload(db_session, store, event, holder, consented=False)

    assert "consent" in str(refused.value)
    assert await db_session.scalar(select(Registration)) is None
    assert await db_session.scalar(select(IdImage)) is None
    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.PURCHASED


async def test_a_person_with_no_ticket_cannot_upload(db_session, store, event, db_engine):
    stranger = await _user(db_session, "zz999", "No Ticket")
    with pytest.raises(service.NoTicket):
        await upload(db_session, store, event, stranger)


async def test_a_second_upload_while_one_is_pending_is_refused(
    db_session, store, event, holder, ticket
):
    await upload(db_session, store, event, holder)
    with pytest.raises(service.UploadNotAllowed) as refused:
        await upload(db_session, store, event, holder)
    assert "already waiting" in str(refused.value)


async def test_an_unreadable_file_costs_a_message_not_an_attempt(
    db_session, store, event, holder, ticket
):
    from app.services.verification.images import UnsupportedImage

    with pytest.raises(UnsupportedImage):
        await upload(db_session, store, event, holder, raw=b"<html>not an image</html>")

    # Nothing was spent and nothing was stored.
    assert await db_session.scalar(select(Registration)) is None
    assert await db_session.scalar(select(IdImage)) is None


async def test_the_same_file_twice_is_flagged_for_a_human_never_auto_refused(
    db_session, store, event, holder, ticket, reviewer
):
    """§9.3: accusing is worse than queuing. Two siblings photographing one
    card on one phone is a support call, not a fraud verdict."""
    first = await upload(db_session, store, event, holder, raw=png_bytes())
    await service.review_verification(
        db_session, event=event, verification_id=first.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=False, note="Try again", allow_resubmit=True,
    )

    second = await upload(db_session, store, event, holder, raw=png_bytes())

    assert service.FLAG_DUPLICATE_IMAGE in second.fraud_flags
    # Flagged, but still a normal open review — not refused.
    assert second.outcome is VerifyOutcome.MANUAL_REVIEW


# ── the retry budget ─────────────────────────────────────────────────────────
async def test_the_attempt_budget_runs_out_and_then_refuses_further_uploads(
    db_session, store, event, holder, ticket, reviewer, monkeypatch
):
    from app.core.settings import get_settings

    monkeypatch.setenv("MAX_ID_UPLOAD_ATTEMPTS", "2")
    get_settings.cache_clear()

    for _ in range(2):
        pending = await upload(db_session, store, event, holder, raw=jpeg_bytes())
        await service.review_verification(
            db_session, event=event, verification_id=pending.id, reviewer=reviewer,
            reviewer_role="ADMIN", approve=False, note="Too blurry", allow_resubmit=True,
        )

    # Out of attempts, the last refusal is final rather than a retry.
    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.REJECTED

    # ...and the person is told they were refused, not that their ticket
    # vanished. A dead ticket still answers.
    with pytest.raises(service.UploadNotAllowed) as refused:
        await upload(db_session, store, event, holder)
    assert "refused" in str(refused.value)

    view = await service.my_verification_view(db_session, event=event, user=holder)
    assert view.can_upload is False
    assert view.ticket_status is TicketStatus.REJECTED
    get_settings.cache_clear()


async def test_asking_for_another_photo_leaves_the_person_able_to_send_one(
    db_session, store, event, holder, ticket, reviewer
):
    first = await upload(db_session, store, event, holder)
    await service.review_verification(
        db_session, event=event, verification_id=first.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=False, note="Hold it flatter", allow_resubmit=True,
    )

    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.RETRY_REQUIRED

    second = await upload(db_session, store, event, holder, raw=png_bytes())
    assert second.attempt_no == 2


async def test_a_deliberate_final_no_refuses_even_with_attempts_left(
    db_session, store, event, holder, ticket, reviewer
):
    first = await upload(db_session, store, event, holder)
    decided = await service.review_verification(
        db_session, event=event, verification_id=first.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=False, note="Not a student ID",
        allow_resubmit=False,
    )

    assert decided.outcome is VerifyOutcome.REJECTED
    assert decided.failure_reasons == ["Not a student ID"]
    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.REJECTED


# ── approving ────────────────────────────────────────────────────────────────
async def test_approving_issues_the_pass_and_links_it_to_the_registration(
    db_session, store, event, holder, ticket, reviewer
):
    """This is the milestone: a verified person has something to scan."""
    pending = await upload(db_session, store, event, holder)

    decided = await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=True,
    )

    assert decided.outcome is VerifyOutcome.APPROVED
    assert decided.reviewed_by == reviewer.id and decided.reviewed_at is not None

    await db_session.refresh(ticket)
    assert ticket.status is TicketStatus.PASS_ISSUED

    issued = await db_session.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    assert issued is not None
    assert issued.registration_id == decided.registration_id
    assert issued.short_code


async def test_a_decision_writes_an_audit_row_naming_the_reviewer(
    db_session, store, event, holder, ticket, reviewer
):
    pending = await upload(db_session, store, event, holder)
    await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=True,
    )

    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ID_REVIEWED")
    )
    assert row is not None
    assert row.actor_user_id == reviewer.id
    assert row.actor_net_id == REVIEWER_NET_ID
    assert row.after["outcome"] == "APPROVED"
    assert row.after["pass_issued"] is True


async def test_the_second_reviewer_to_decide_the_same_row_loses(
    db_session, store, event, holder, ticket, reviewer
):
    pending = await upload(db_session, store, event, holder)
    await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=True,
    )

    with pytest.raises(service.AlreadyDecided):
        await service.review_verification(
            db_session, event=event, verification_id=pending.id, reviewer=reviewer,
            reviewer_role="ADMIN", approve=False, allow_resubmit=False,
        )


async def test_an_already_verified_person_cannot_upload_again(
    db_session, store, event, holder, ticket, reviewer
):
    pending = await upload(db_session, store, event, holder)
    await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=True,
    )

    with pytest.raises(service.UploadNotAllowed) as refused:
        await upload(db_session, store, event, holder, raw=png_bytes())
    assert "already verified" in str(refused.value)


# ── the queue ────────────────────────────────────────────────────────────────
async def test_the_queue_holds_open_reviews_oldest_first_and_drops_decided_ones(
    db_session, store, event, holder, ticket, reviewer
):
    pending = await upload(db_session, store, event, holder)

    queue = await service.review_queue_view(db_session, event_id=event.id, limit=50, offset=0)
    assert queue.pending == 1
    item = queue.items[0]
    assert item.verification_id == pending.id
    assert item.full_name == "Aarav Sharma"
    assert item.net_id == HOLDER_NET_ID
    assert item.ticket_status is TicketStatus.MANUAL_REVIEW
    # A same-origin API path, not an object-store URL: the local backend cannot
    # sign one, and what it returned instead was unreachable from a browser.
    # test_verification_routes.py proves this path actually serves the bytes.
    assert item.id_image_url.startswith("/api/v1/id-images/")

    await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=True,
    )
    after = await service.review_queue_view(db_session, event_id=event.id, limit=50, offset=0)
    assert after.pending == 0 and after.items == []


async def test_a_purged_image_yields_no_url_rather_than_a_broken_link(
    db_session, store, event, holder, ticket
):
    """T+45 days the bytes go. The row stays, so the check is still provable,
    and the reviewer sees nothing rather than a dead link."""
    pending = await upload(db_session, store, event, holder)
    await db_session.execute(
        text("UPDATE id_images SET purged_at = now() WHERE id = :id"),
        {"id": pending.id_image_id},
    )

    href = await service.image_href(db_session, id_image_id=pending.id_image_id)
    assert href is None


# ── what the person is told ──────────────────────────────────────────────────
async def test_the_participant_view_never_leaks_reviewer_internals(
    db_session, store, event, holder, ticket, reviewer
):
    pending = await upload(db_session, store, event, holder, raw=png_bytes())
    await service.review_verification(
        db_session, event=event, verification_id=pending.id, reviewer=reviewer,
        reviewer_role="ADMIN", approve=False, note="Glare on the photo", allow_resubmit=True,
    )

    view = await service.my_verification_view(db_session, event=event, user=holder)

    assert view.ticket_status is TicketStatus.RETRY_REQUIRED
    assert view.outcome is VerifyOutcome.RETRY_REQUIRED
    assert view.review_note == "Glare on the photo"  # actionable, so shared
    assert view.can_upload is True
    assert view.attempts_used == 1
    # No image URL, no fraud flags, no reviewer identity on this model at all.
    assert not hasattr(view, "id_image_url")
    assert not hasattr(view, "fraud_flags")
    assert not hasattr(view, "reviewed_by")


async def test_a_pending_review_tells_the_person_to_wait_not_to_upload_again(
    db_session, store, event, holder, ticket
):
    await upload(db_session, store, event, holder)
    view = await service.my_verification_view(db_session, event=event, user=holder)

    assert view.can_upload is False
    assert "waiting to be checked" in view.blocked_reason
    # An open review's note is not the participant's business.
    assert view.review_note is None
