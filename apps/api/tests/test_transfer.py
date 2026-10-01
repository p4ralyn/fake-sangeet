"""Pass transfer, and the deadline that ends it.

The client's rule: transfers stop three hours before doors — 15:00 IST on
15 October, against an 18:00 start. It is enforced on every request from
`events.starts_at` minus a configured lead time, not from a written-down
timestamp, so the two can never disagree.

The invariants a transfer must not break:
  * the old holder's QR stops working the moment the ticket moves;
  * the new holder starts the ID check again, because the check exists to match
    a face to a pass and the face just changed;
  * one person still holds at most one live ticket;
  * the previous holder's verification record survives as history.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models import (
    AuditLog,
    Event,
    Order,
    Pass,
    PassState,
    Registration,
    Ticket,
    TicketStatus,
    User,
)
from app.services.passes.issue import issue_pass
from app.services.passes.transfer import (
    TransferRefused,
    closes_at,
    is_open,
    normalise_net_id,
    transfer_ticket,
)

HOLDER = "pk944"
THIRD = "ms101"
RECIPIENT = "zq202"


async def _user(db_session, net_id: str) -> User:
    user = User(net_id=net_id, email=f"{net_id}@snu.edu.in", display_name=net_id.upper())
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def event(db_session, event_id) -> Event:
    """An event that starts in 30 days, so transfer is wide open by default."""
    return await db_session.get(Event, event_id)


@pytest_asyncio.fixture
async def holder(db_session) -> User:
    return await _user(db_session, HOLDER)


@pytest_asyncio.fixture
async def ticket(db_session, event_id) -> Ticket:
    order = Order(
        event_id=event_id,
        external_id="transfer-row",
        purchaser_email=f"{HOLDER}@snu.edu.in",
        purchaser_name="Aarav Sharma",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    row = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref="FS26-XFER-1",
        status=TicketStatus.PASS_ISSUED,
        bound_net_id=HOLDER,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest_asyncio.fixture
async def issued(db_session, event, ticket) -> Pass:
    return await issue_pass(db_session, event=event, ticket=ticket)


# ── the deadline ─────────────────────────────────────────────────────────────
def test_the_deadline_is_three_hours_before_doors(db_session):
    """The client's own number, derived rather than written down: an 18:00
    start gives a 15:00 cut-off."""
    event = Event(
        slug="cutoff",
        name="Fake Sangeet",
        starts_at=datetime(2026, 10, 15, 18, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 15, 22, 0, tzinfo=UTC),
        config={},
    )
    assert closes_at(event) == datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


def test_moving_the_event_moves_the_deadline_with_it():
    """Why it is derived: a hardcoded 15 October timestamp would survive a date
    change and quietly close transfers on the wrong day."""
    base = dict(slug="s", name="n", config={})
    early = Event(**base, starts_at=datetime(2026, 10, 15, 18, tzinfo=UTC),
                  ends_at=datetime(2026, 10, 15, 22, tzinfo=UTC))
    moved = Event(**base, starts_at=datetime(2026, 11, 20, 20, tzinfo=UTC),
                  ends_at=datetime(2026, 11, 20, 23, tzinfo=UTC))
    assert closes_at(moved) - closes_at(early) == timedelta(days=36, hours=2)


def test_the_lead_time_is_configurable_per_event():
    event = Event(
        slug="s", name="n",
        starts_at=datetime(2026, 10, 15, 18, tzinfo=UTC),
        ends_at=datetime(2026, 10, 15, 22, tzinfo=UTC),
        config={"transfer": {"closes_before_minutes": 60}},
    )
    assert closes_at(event) == datetime(2026, 10, 15, 17, 0, tzinfo=UTC)


async def test_transfer_is_refused_after_the_deadline(db_session, event, holder, ticket):
    """The whole point of the cut-off: it does not rely on anybody remembering
    to switch something off at 3pm on the day."""
    event.starts_at = datetime.now(UTC) + timedelta(hours=2)  # deadline is past
    await db_session.flush()
    assert is_open(event) is False

    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
        )
    assert "closed three hours before" in str(refused.value)

    # Nothing moved.
    await db_session.refresh(ticket)
    assert ticket.bound_net_id == HOLDER


async def test_transfer_is_open_right_up_to_the_deadline(db_session, event, holder, ticket):
    event.starts_at = datetime.now(UTC) + timedelta(hours=3, minutes=1)
    await db_session.flush()
    assert is_open(event) is True

    result = await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )
    assert result.to_net_id == RECIPIENT


# ── what a transfer does ─────────────────────────────────────────────────────
async def test_a_transfer_moves_the_ticket_and_resets_the_id_check(
    db_session, event, holder, ticket, issued
):
    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )

    await db_session.refresh(ticket)
    assert ticket.bound_net_id == RECIPIENT
    # Unbound from the old account, so the previous holder stops seeing it.
    assert ticket.user_id is None
    # Back to the start of the funnel: the new holder uploads their own ID.
    assert ticket.status is TicketStatus.PURCHASED


async def test_the_old_holders_qr_stops_working_immediately(
    db_session, event, holder, ticket, issued
):
    """The transfer is only safe if the QR already on somebody's phone dies
    without needing their cooperation. That is what `serial` is for."""
    before = issued.serial

    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )

    await db_session.refresh(issued)
    assert issued.serial == before + 1
    assert issued.current_state is PassState.REVOKED
    assert issued.revoked_reason == "TRANSFERRED"
    assert issued.revoked_at is not None


async def test_the_previous_holders_verification_is_kept_as_history(
    db_session, event, holder, ticket, issued
):
    """Deleting it would destroy the record that somebody's ID was checked, to
    save a row. It is superseded instead."""
    registration = Registration(
        event_id=event.id,
        ticket_id=ticket.id,
        user_id=holder.id,
        full_name="Aarav Sharma",
        net_id=HOLDER,
        email=f"{HOLDER}@snu.edu.in",
        consent_version="v1",
        consented_at=datetime.now(UTC),
    )
    db_session.add(registration)
    await db_session.flush()

    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )

    await db_session.refresh(registration)
    assert registration.superseded_at is not None
    # Still there — and no longer the answer to "who holds this ticket".
    from app.services.verification.service import get_registration

    assert await get_registration(db_session, ticket_id=ticket.id) is None


async def test_the_new_holder_can_register_even_though_the_old_one_did(
    db_session, event, holder, ticket
):
    """The partial unique index exists for exactly this moment."""
    first = Registration(
        event_id=event.id, ticket_id=ticket.id, user_id=holder.id,
        full_name="Aarav", net_id=HOLDER, email=f"{HOLDER}@snu.edu.in",
        consent_version="v1", consented_at=datetime.now(UTC),
    )
    db_session.add(first)
    await db_session.flush()

    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )
    recipient = await _user(db_session, RECIPIENT)
    second = Registration(
        event_id=event.id, ticket_id=ticket.id, user_id=recipient.id,
        full_name="Ishaan", net_id=RECIPIENT, email=f"{RECIPIENT}@snu.edu.in",
        consent_version="v1", consented_at=datetime.now(UTC),
    )
    db_session.add(second)
    await db_session.flush()  # the old UNIQUE would have refused this

    rows = (await db_session.scalars(select(Registration))).all()
    assert len(rows) == 2


async def test_a_transfer_writes_an_audit_row_with_both_sides_hashed(
    db_session, event, holder, ticket, issued
):
    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )

    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "PASS_TRANSFERRED")
    )
    assert row is not None
    assert row.actor_net_id == HOLDER
    assert row.object_id == ticket.id
    # Who gave their ticket to whom is recorded, but not in plain text.
    assert row.before["net_id"].startswith("sha256:")
    assert row.after["net_id"].startswith("sha256:")
    assert RECIPIENT not in str(row.after)
    assert row.after["pass_revoked"] is True


async def test_approving_the_new_holder_revives_the_pass_rather_than_stranding_it(
    db_session, event, holder, ticket, issued
):
    """`passes.ticket_id` is UNIQUE, so a reissue is this row coming back to
    life with a higher serial — not a second pass."""
    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )
    revoked_serial = issued.serial

    revived = await issue_pass(db_session, event=event, ticket=ticket)

    assert revived.id == issued.id
    assert revived.current_state is PassState.NOT_ARRIVED
    assert revived.serial == revoked_serial + 1
    assert revived.revoked_at is None
    assert (await db_session.scalar(select(Pass.id).where(Pass.ticket_id == ticket.id)))


# ── who may transfer to whom ─────────────────────────────────────────────────
async def test_you_cannot_transfer_to_somebody_who_already_has_a_ticket(
    db_session, event, holder, ticket, event_id
):
    """One person, one pass — the same rule `tickets_one_per_net_id` enforces,
    refused here as a sentence instead of a constraint violation."""
    order = await db_session.scalar(select(Order))
    db_session.add(
        Ticket(
            event_id=event_id, order_id=order.id, ticket_ref="FS26-XFER-2",
            status=TicketStatus.PASS_ISSUED, bound_net_id=RECIPIENT,
        )
    )
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
        )
    assert "already has a ticket" in str(refused.value)


async def test_you_cannot_transfer_to_yourself(db_session, event, holder, ticket):
    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=holder, ticket=ticket, to_net_id=HOLDER
        )
    assert "your own NetID" in str(refused.value)


@pytest.mark.parametrize(
    "bad", ["", "  ", "x", "a" * 80, "zzzz107", "ab1234", "abc12", "student@gmail.com"]
)
async def test_an_unusable_netid_is_refused_before_anything_moves(
    db_session, event, holder, ticket, bad
):
    with pytest.raises(TransferRefused):
        await transfer_ticket(
            db_session, event=event, holder=holder, ticket=ticket, to_net_id=bad
        )
    await db_session.refresh(ticket)
    assert ticket.bound_net_id == HOLDER


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("ZQ202", "zq202"), ("  zq202 ", "zq202"), ("zq202@snu.edu.in", "zq202")],
)
def test_a_pasted_email_is_accepted_as_a_netid(typed, expected):
    """Half the people typing this will paste an address."""
    assert normalise_net_id(typed) == expected


async def test_a_dead_ticket_cannot_be_transferred(db_session, event, holder, ticket):
    ticket.status = TicketStatus.REVOKED
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
        )
    assert "can’t be transferred" in str(refused.value)


# ── through the API ──────────────────────────────────────────────────────────
async def login(client, net_id: str) -> dict[str, str]:
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )
    return {"x-csrf-token": client.cookies.get("ug_csrf")}


@pytest_asyncio.fixture
async def committed_ticket(db_session, event_id):
    order = Order(
        event_id=event_id, external_id="api-xfer", purchaser_email=f"{HOLDER}@snu.edu.in",
        purchaser_name="Aarav Sharma", quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    row = Ticket(
        event_id=event_id, order_id=order.id, ticket_ref="FS26-API-XFER",
        status=TicketStatus.PASS_ISSUED, bound_net_id=HOLDER,
    )
    db_session.add(row)
    await db_session.flush()
    event = await db_session.get(Event, event_id)
    await issue_pass(db_session, event=event, ticket=row)
    await db_session.commit()
    return row


async def test_the_pass_response_tells_the_screen_when_transfer_closes(
    client, committed_ticket
):
    headers = await login(client, HOLDER)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert body["transfer"]["open"] is True
    assert body["transfer"]["closes_at"]


async def test_transferring_over_http_moves_the_ticket(client, db_session, committed_ticket):
    headers = await login(client, HOLDER)

    response = await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )

    assert response.status_code == 200
    assert response.json()["to_net_id"] == RECIPIENT
    assert response.json()["verification_required"] is True

    # The old holder no longer sees it...
    mine = (await client.get("/api/v1/me/pass", headers=headers)).json()
    assert mine["tickets"] == []

    # ...and the new one does, with no QR until they are verified.
    await db_session.execute(text("SELECT 1"))
    client.cookies.clear()
    theirs_headers = await login(client, RECIPIENT)
    theirs = (await client.get("/api/v1/me/pass", headers=theirs_headers)).json()
    assert len(theirs["tickets"]) == 1
    assert theirs["tickets"][0]["qr_token"] is None
    assert theirs["tickets"][0]["state"] == "ID_PENDING"


async def test_the_new_holder_sees_their_own_name_not_the_previous_holders(
    client, committed_ticket
):
    """The superseded registration is gone from the lookup and `user_id` is
    cleared, so a naive fallback lands on the ORDER's purchaser — who is the
    person that just gave the ticket away."""
    headers = await login(client, HOLDER)
    await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )
    client.cookies.clear()

    theirs = await login(client, RECIPIENT)
    body = (await client.get("/api/v1/me/pass", headers=theirs)).json()

    assert body["tickets"][0]["holder_name"].lower() == RECIPIENT
    assert HOLDER not in body["tickets"][0]["holder_name"].lower()


async def test_transferring_needs_a_csrf_token(client, committed_ticket):
    await login(client, HOLDER)
    response = await client.post(
        "/api/v1/me/pass/transfer", json={"to_net_id": RECIPIENT}
    )
    assert response.status_code == 403


async def test_transferring_without_a_ticket_is_a_404(client, event_id):
    headers = await login(client, "zz999")
    response = await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )
    assert response.status_code == 404


async def test_a_refused_transfer_comes_back_as_a_409_with_a_readable_reason(
    client, committed_ticket
):
    headers = await login(client, HOLDER)
    response = await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": HOLDER}
    )
    assert response.status_code == 409
    assert "your own NetID" in response.json()["error"]["message"]


async def test_the_transferred_pass_is_refused_at_the_gate(
    client, db_session, committed_ticket, event_id
):
    """End to end: the token the old holder is still holding must not open the
    door after they have given the ticket away."""
    from uuid import uuid4

    from app.services.passes.token import mint_pass_token

    issued_pass = await db_session.scalar(
        select(Pass).where(Pass.ticket_id == committed_ticket.id)
    )
    stale = mint_pass_token(
        event_id=event_id,
        pass_id=issued_pass.id,
        serial=issued_pass.serial,
        expires_at=issued_pass.expires_at,
    )

    headers = await login(client, HOLDER)
    await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )
    client.cookies.clear()

    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) "
            "VALUES (gen_random_uuid(), :e, 'zq203', 'SECURITY')"
        ),
        {"e": event_id},
    )
    await db_session.commit()
    guard = await login(client, "zq203")
    scanned = await client.post(
        "/api/v1/scans",
        headers=guard,
        json={
            "token": stale,
            "client_scan_uuid": str(uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )

    assert scanned.status_code in (403, 409)
    assert scanned.json()["error"]["code"] in ("PASS_REVOKED", "PASS_SUPERSEDED")


# ── one transfer, ever ───────────────────────────────────────────────────────
async def test_a_ticket_can_only_be_transferred_once(db_session, event, holder, ticket):
    """Each transfer sends a new person back through ID review. A ticket that
    keeps moving is one nobody at the desk can account for."""
    from app.models import MAX_TRANSFERS_PER_TICKET

    assert MAX_TRANSFERS_PER_TICKET == 1

    await transfer_ticket(
        db_session, event=event, holder=holder, ticket=ticket, to_net_id=RECIPIENT
    )
    await db_session.refresh(ticket)
    assert ticket.transfer_count == 1

    recipient = await _user(db_session, RECIPIENT)
    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=recipient, ticket=ticket, to_net_id=THIRD
        )
    assert "only be transferred once" in str(refused.value)

    # ...and nothing moved on the refusal.
    await db_session.refresh(ticket)
    assert ticket.bound_net_id == RECIPIENT
    assert ticket.transfer_count == 1


async def test_the_database_refuses_a_second_transfer_even_if_the_service_does_not(
    db_session, event, holder, ticket
):
    """The service check is for the message; the CHECK constraint is the
    guarantee. If the Python guard were ever edited around or lost to a race,
    this is what still holds."""
    from sqlalchemy.exc import IntegrityError

    ticket.transfer_count = 2  # what a bypassed guard would write
    with pytest.raises(IntegrityError) as refused:
        await db_session.flush()
    assert "tickets_transfer_limit" in str(refused.value)
    await db_session.rollback()


async def test_a_fresh_ticket_starts_with_no_transfers(db_session, ticket):
    assert ticket.transfer_count == 0


async def test_the_pass_view_reports_the_transfer_budget(client, committed_ticket):
    headers = await login(client, HOLDER)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert body["transfer"]["transfers_used"] == 0
    assert body["transfer"]["max_transfers"] == 1

    await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )
    client.cookies.clear()

    # The NEW holder must be told they cannot pass it on.
    theirs = await login(client, RECIPIENT)
    after = (await client.get("/api/v1/me/pass", headers=theirs)).json()
    assert after["transfer"]["transfers_used"] == 1
    assert after["transfer"]["max_transfers"] == 1


async def test_the_second_transfer_is_refused_over_http(client, committed_ticket):
    headers = await login(client, HOLDER)
    await client.post(
        "/api/v1/me/pass/transfer", headers=headers, json={"to_net_id": RECIPIENT}
    )
    client.cookies.clear()

    theirs = await login(client, RECIPIENT)
    response = await client.post(
        "/api/v1/me/pass/transfer", headers=theirs, json={"to_net_id": THIRD}
    )

    assert response.status_code == 409
    assert "only be transferred once" in response.json()["error"]["message"]
