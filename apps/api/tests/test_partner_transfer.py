"""Sending a PROXY couple's partner seat to the partner.

A PROXY couple is a sheet row that paid for two seats but named nobody for the
second, so the second ticket is minted unbound and rides on the buyer's
account. It has no QR — passes are issued when somebody's own ID is approved,
and nobody owns that seat — so the pass screen shows it LOCKED, with one way
out: send it to the partner, who signs in as themselves and is checked like
everybody else.

The rules this file pins down (client, 1 Oct 2026, amended 9 Oct 2026):
  * only a PROXY holder has a partner seat to send, and it can be sent once;
  * the partner who receives it CAN pass it on, once — the send is the seat
    reaching its first owner, not its transfer; every ticket is freehold, the
    second person's included (9 Oct; replaced "can never pass it on");
  * a holder who transfers their OWN ticket while the seat is unsent takes the
    seat along, and the new holder can still send it: moving with the bundle is
    not the seat's transfer;
  * the recipient must be a real NetID, must not be you, and must not already
    hold a ticket; and the 15:00 deadline applies.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    AuditLog,
    EmailOutbox,
    Event,
    Order,
    PartnerMode,
    Ticket,
    TicketStatus,
    User,
)
from app.services.mailer.outbox import TEMPLATE_PASS_RECEIVED
from app.services.passes.issue import issue_pass
from app.services.passes.transfer import (
    TransferRefused,
    transfer_partner_seat,
    transfer_ticket,
)

BUYER = "pk944"
PARTNER = "zq202"
NEW_HOLDER = "ms101"
SOMEONE = "zq204"


async def _user(db_session, net_id: str) -> User:
    user = User(net_id=net_id, email=f"{net_id}@snu.edu.in", display_name=net_id.upper())
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def event(db_session, event_id) -> Event:
    return await db_session.get(Event, event_id)


@pytest_asyncio.fixture
async def buyer(db_session) -> User:
    return await _user(db_session, BUYER)


async def _proxy_couple(db_session, event: Event) -> tuple[Ticket, Ticket]:
    """Exactly as M3 mints a PROXY row: seat 1 bound to the buyer, seat 2
    bound to nobody, both in one order. The buyer's ID is already checked."""
    order = Order(
        event_id=event.id, external_id="proxy-row", purchaser_email=f"{BUYER}@snu.edu.in",
        purchaser_name="Aarav Sharma", quantity=2, ticket_type="COUPLE",
    )
    db_session.add(order)
    await db_session.flush()
    first = Ticket(
        event_id=event.id, order_id=order.id, ticket_ref="FS26-PROXY-1", seq_in_order=1,
        status=TicketStatus.PASS_ISSUED, partner_mode=PartnerMode.PROXY, bound_net_id=BUYER,
    )
    second = Ticket(
        event_id=event.id, order_id=order.id, ticket_ref="FS26-PROXY-2", seq_in_order=2,
        status=TicketStatus.PURCHASED, partner_mode=PartnerMode.PROXY,
    )
    db_session.add_all([first, second])
    await db_session.flush()
    await issue_pass(db_session, event=event, ticket=first)
    return first, second


@pytest_asyncio.fixture
async def couple(db_session, event) -> tuple[Ticket, Ticket]:
    return await _proxy_couple(db_session, event)


# ── the service ──────────────────────────────────────────────────────────────
async def test_a_proxy_buyer_sends_the_partner_seat_and_keeps_their_own(
    db_session, event, buyer, couple
):
    first, second = couple

    result = await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)

    await db_session.refresh(first)
    await db_session.refresh(second)
    assert result.to_net_id == PARTNER
    assert result.ticket_ref == second.ticket_ref
    assert second.bound_net_id == PARTNER
    assert second.status is TicketStatus.PURCHASED  # the partner's own ID check
    assert second.transfer_count == 0  # freehold: the send is not its transfer
    # The buyer's own seat has not moved and still has its transfer to spend.
    assert first.bound_net_id == BUYER
    assert first.transfer_count == 0


async def test_the_partner_who_receives_it_can_pass_it_on_once(
    db_session, event, buyer, couple
):
    """Freehold (9 Oct 2026): the seat's one transfer is the partner's to use,
    and after that it is spent like anybody else's."""
    _, second = couple
    await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    partner = await _user(db_session, PARTNER)
    # The partner's own ID approved: passing on needs that first (10 Oct 2026).
    second.status = TicketStatus.PASS_ISSUED
    await db_session.flush()

    await transfer_ticket(
        db_session, event=event, holder=partner, ticket=second, to_net_id=SOMEONE
    )
    await db_session.refresh(second)
    assert second.bound_net_id == SOMEONE
    assert second.transfer_count == 1

    someone = await _user(db_session, SOMEONE)
    second.status = TicketStatus.PASS_ISSUED  # approved too: only the cap refuses now
    await db_session.flush()
    with pytest.raises(TransferRefused) as refused:
        await transfer_ticket(
            db_session, event=event, holder=someone, ticket=second, to_net_id=NEW_HOLDER
        )
    assert "only be transferred once" in str(refused.value)


async def test_the_partner_seat_can_only_be_sent_once(db_session, event, buyer, couple):
    await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=SOMEONE)
    assert "no partner pass" in str(refused.value)


async def test_a_single_ticket_has_no_partner_seat_to_send(db_session, event, buyer):
    order = Order(
        event_id=event.id, external_id="single-row", purchaser_email=f"{BUYER}@snu.edu.in",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Ticket(
            event_id=event.id, order_id=order.id, ticket_ref="FS26-SINGLE-1",
            status=TicketStatus.PASS_ISSUED, bound_net_id=BUYER,
        )
    )
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    assert "no partner pass" in str(refused.value)


async def test_a_self_claim_partner_is_not_the_buyers_to_send(db_session, event, buyer, couple):
    """A SELF_CLAIM partner seat is already bound to the partner. It is theirs,
    not something the buyer can redirect."""
    _, second = couple
    second.partner_mode = PartnerMode.SELF_CLAIM
    second.bound_net_id = PARTNER
    await db_session.flush()

    with pytest.raises(TransferRefused):
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=SOMEONE)
    await db_session.refresh(second)
    assert second.bound_net_id == PARTNER


@pytest.mark.parametrize("bad", ["zzzz107", "abc", "a1234", "", "student@gmail.com"])
async def test_the_partner_must_be_a_real_netid(db_session, event, buyer, couple, bad):
    _, second = couple
    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=bad)
    assert "NetID" in str(refused.value)
    await db_session.refresh(second)
    assert second.bound_net_id is None


async def test_the_partner_cannot_be_you(db_session, event, buyer, couple):
    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=BUYER)
    assert "your own NetID" in str(refused.value)


async def test_the_partner_cannot_already_hold_a_ticket(db_session, event, buyer, couple):
    first, _ = couple
    db_session.add(
        Ticket(
            event_id=event.id, order_id=first.order_id, ticket_ref="FS26-OTHER-1",
            status=TicketStatus.PASS_ISSUED, bound_net_id=PARTNER,
        )
    )
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    assert "already has a ticket" in str(refused.value)


async def test_the_partner_seat_closes_with_everything_else(db_session, event, buyer, couple):
    """Client decision: after 15:00 an unsent seat stays locked."""
    event.starts_at = datetime.now(UTC) + timedelta(hours=2)
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    assert "closed" in str(refused.value)


async def test_your_own_transfer_takes_the_unsent_seat_and_it_stays_sendable(
    db_session, event, buyer, couple
):
    """'Transfer your ticket' on a PROXY couple hands over both QRs. The seat
    rides along unbound, so whoever now holds the couple can still send it."""
    first, second = couple
    await transfer_ticket(db_session, event=event, holder=buyer, ticket=first, to_net_id=NEW_HOLDER)
    new_holder = await _user(db_session, NEW_HOLDER)
    first.status = TicketStatus.PASS_ISSUED  # the new holder's own ID approved
    await db_session.flush()

    result = await transfer_partner_seat(
        db_session, event=event, holder=new_holder, to_net_id=PARTNER
    )

    await db_session.refresh(second)
    assert result.ticket_ref == second.ticket_ref
    assert second.bound_net_id == PARTNER
    assert second.transfer_count == 0


async def test_the_previous_holder_has_nothing_left_to_send(db_session, event, buyer, couple):
    first, _ = couple
    await transfer_ticket(db_session, event=event, holder=buyer, ticket=first, to_net_id=NEW_HOLDER)

    with pytest.raises(TransferRefused):
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)


async def test_sending_the_seat_writes_an_audit_row_with_both_sides_hashed(
    db_session, event, buyer, couple
):
    await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)

    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "PARTNER_SEAT_TRANSFERRED")
    )
    assert row is not None
    assert BUYER not in str(row.before) and PARTNER not in str(row.after)


# ── through the API ──────────────────────────────────────────────────────────
async def login(client, net_id: str) -> dict[str, str]:
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )
    return {"x-csrf-token": client.cookies.get("ug_csrf")}


@pytest_asyncio.fixture
async def committed_couple(db_session, event) -> tuple[Ticket, Ticket]:
    pair = await _proxy_couple(db_session, event)
    await db_session.commit()
    return pair


async def test_the_pass_view_locks_the_unsent_seat_and_offers_to_send_it(
    client, committed_couple
):
    headers = await login(client, BUYER)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    own, partner = body["tickets"]
    assert own["locked"] is False and own["qr_token"]
    assert partner["locked"] is True
    assert partner["qr_token"] is None and partner["short_code"] == ""
    assert body["partner_transfer"]["open"] is True
    # The buyer's own transfer budget is the buyer's own ticket's.
    assert body["transfer"]["transfers_used"] == 0


async def test_sending_over_http_leaves_one_pass_each(client, committed_couple):
    headers = await login(client, BUYER)
    sent = await client.post(
        "/api/v1/me/pass/transfer-partner", headers=headers, json={"to_net_id": PARTNER}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["to_net_id"] == PARTNER

    mine = (await client.get("/api/v1/me/pass", headers=headers)).json()
    assert [t["seat_index"] for t in mine["tickets"]] == [1]
    assert mine["partner_transfer"] is None
    client.cookies.clear()

    theirs_headers = await login(client, PARTNER)
    theirs = (await client.get("/api/v1/me/pass", headers=theirs_headers)).json()
    assert len(theirs["tickets"]) == 1
    seat = theirs["tickets"][0]
    assert seat["seat_index"] == 2 and seat["locked"] is False
    assert seat["state"] == "ID_PENDING"  # their own ID check comes first
    # Freehold (9 Oct 2026): the partner still has the seat's one transfer.
    assert theirs["transfer"]["transfers_used"] == 0
    assert theirs["transfer"]["max_transfers"] == 1
    assert theirs["partner_transfer"] is None


async def test_the_partner_route_needs_a_csrf_token(client, committed_couple):
    await login(client, BUYER)
    response = await client.post("/api/v1/me/pass/transfer-partner", json={"to_net_id": PARTNER})
    assert response.status_code == 403


async def test_a_refused_partner_send_is_a_409_with_a_reason(client, committed_couple):
    headers = await login(client, BUYER)
    response = await client.post(
        "/api/v1/me/pass/transfer-partner", headers=headers, json={"to_net_id": "zzzz107"}
    )
    assert response.status_code == 409
    assert "NetID" in response.json()["error"]["message"]


# ── only after your own ID is approved ───────────────────────────────────────
async def test_the_partner_seat_cannot_be_sent_before_your_own_id_is_approved(
    db_session, event, buyer, couple
):
    first, second = couple
    first.status = TicketStatus.PURCHASED
    await db_session.flush()

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    assert "after your own ID has been checked and approved" in str(refused.value)
    await db_session.refresh(second)
    assert second.bound_net_id is None


async def test_a_new_holder_must_be_approved_before_sending_the_seat_on(
    db_session, event, buyer, couple
):
    first, _ = couple
    await transfer_ticket(db_session, event=event, holder=buyer, ticket=first, to_net_id=NEW_HOLDER)
    new_holder = await _user(db_session, NEW_HOLDER)

    with pytest.raises(TransferRefused) as refused:
        await transfer_partner_seat(
            db_session, event=event, holder=new_holder, to_net_id=PARTNER
        )
    assert "after your own ID has been checked and approved" in str(refused.value)


# ── telling the partner ──────────────────────────────────────────────────────
async def _received(db_session) -> list[EmailOutbox]:
    return list(
        await db_session.scalars(
            select(EmailOutbox).where(EmailOutbox.template == TEMPLATE_PASS_RECEIVED)
        )
    )


async def test_sending_the_seat_emails_the_partner(db_session, event, buyer, couple):
    await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)

    mail = await _received(db_session)
    assert [m.to_email for m in mail] == [f"{PARTNER}@snu.edu.in"]
    assert mail[0].payload["kind"] == "partner_seat"


async def test_every_new_holder_of_a_seat_is_emailed_not_just_the_first(
    db_session, event, buyer, couple
):
    """15 Oct 2026: a partner seat sent and then passed on reaches two new
    holders. Keyed on the ticket ref alone, the second email collided with the
    first and was silently dropped -- four people never heard."""
    _, second = couple
    await transfer_partner_seat(db_session, event=event, holder=buyer, to_net_id=PARTNER)
    partner = await _user(db_session, PARTNER)
    second.status = TicketStatus.PASS_ISSUED  # the partner's own ID approved
    await db_session.flush()
    await transfer_ticket(
        db_session, event=event, holder=partner, ticket=second, to_net_id=SOMEONE
    )

    mail = await _received(db_session)
    assert sorted(m.to_email for m in mail) == sorted(
        [f"{PARTNER}@snu.edu.in", f"{SOMEONE}@snu.edu.in"]
    )


async def test_a_couple_transfer_tells_the_new_holder_the_second_pass_came_too(
    db_session, event, buyer, couple
):
    first, _ = couple
    await transfer_ticket(
        db_session, event=event, holder=buyer, ticket=first, to_net_id=NEW_HOLDER
    )

    mail = await _received(db_session)
    assert [m.to_email for m in mail] == [f"{NEW_HOLDER}@snu.edu.in"]
    assert mail[0].payload == {"kind": "transfer", "brings_partner_seat": True}
