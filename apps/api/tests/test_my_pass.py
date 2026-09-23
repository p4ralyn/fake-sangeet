"""`GET /api/v1/me/pass` — the participant's own pass and QR token (M6).

This is the endpoint that makes the scanner useful to a real person: what it
returns is what the phone draws, and what the phone draws is what the door
scans. So the test that matters most is the round trip — mint here, scan there.
"""
import pytest_asyncio
from sqlalchemy import select, text

from app.models import (
    Event,
    Order,
    PartnerMode,
    Pass,
    PassState,
    Ticket,
    TicketStatus,
)
from app.services.passes.issue import issue_pass

HOLDER_NET_ID = "pk944"
SECURITY_NET_ID = "zq203"


async def login(client, net_id: str) -> dict[str, str]:
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )
    return {"x-csrf-token": client.cookies.get("ug_csrf")}


@pytest_asyncio.fixture
async def staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) "
            "VALUES (gen_random_uuid(), :e, :sec, 'SECURITY')"
        ),
        {"e": event_id, "sec": SECURITY_NET_ID},
    )
    await db_session.commit()


async def make_ticket(
    db_session, event_id, *, seats=1, seq=1, order=None, status=None, partner_mode=None
):
    if order is None:
        order = Order(
            event_id=event_id,
            external_id=f"pass-row-{seq}",
            purchaser_email=f"{HOLDER_NET_ID}@snu.edu.in",
            purchaser_name="Aarav Sharma",
            quantity=seats,
            ticket_type="COUPLE" if seats == 2 else "SINGLE",
        )
        db_session.add(order)
        await db_session.flush()
    ticket = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref=f"FS26-PASS-{seq}",
        seq_in_order=seq,
        status=status or TicketStatus.PASS_ISSUED,
        partner_mode=partner_mode,
        bound_net_id=HOLDER_NET_ID if seq == 1 else None,
    )
    db_session.add(ticket)
    await db_session.flush()
    return order, ticket


@pytest_asyncio.fixture
async def issued(db_session, event_id):
    event = await db_session.get(Event, event_id)
    _, ticket = await make_ticket(db_session, event_id)
    issued_pass = await issue_pass(db_session, event=event, ticket=ticket)
    await db_session.commit()
    return issued_pass


# ── what a person gets ───────────────────────────────────────────────────────
async def test_the_pass_endpoint_returns_a_token_and_a_short_code(client, issued):
    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert len(body["tickets"]) == 1
    ticket = body["tickets"][0]
    assert ticket["short_code"] == issued.short_code
    assert ticket["qr_token"].startswith("UG1.")
    assert ticket["state"] == "ISSUED"
    assert ticket["seat_index"] == 1


async def test_the_token_this_endpoint_mints_actually_scans(client, db_session, staff, issued):
    """The round trip. A token the door refuses is worse than no token: the
    person is standing there holding a phone that says everything is fine."""
    from datetime import UTC, datetime
    from uuid import uuid4

    participant = await login(client, HOLDER_NET_ID)
    token = (await client.get("/api/v1/me/pass", headers=participant)).json()["tickets"][0][
        "qr_token"
    ]
    client.cookies.clear()

    guard = await login(client, SECURITY_NET_ID)
    scanned = await client.post(
        "/api/v1/scans",
        headers=guard,
        json={
            "token": token,
            "client_scan_uuid": str(uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )

    assert scanned.status_code == 200
    assert scanned.json()["status"] == "DONE"
    assert scanned.json()["action"] == "CHECK_IN"
    assert scanned.json()["state"] == "INSIDE"


async def test_signing_in_is_required(client, issued):
    assert (await client.get("/api/v1/me/pass")).status_code == 401


async def test_somebody_elses_ticket_is_not_visible(client, issued):
    headers = await login(client, "zz999")
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()
    assert body["tickets"] == []


# ── the states a person can be in ────────────────────────────────────────────
async def test_a_ticket_with_no_pass_yet_still_appears_with_its_real_state(
    client, db_session, event_id
):
    """Hiding it would read as a lost ticket. "ID check pending" is exactly
    what the person needs to see."""
    await make_ticket(db_session, event_id, status=TicketStatus.MANUAL_REVIEW)
    await db_session.commit()

    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert len(body["tickets"]) == 1
    assert body["tickets"][0]["state"] == "MANUAL_REVIEW"
    assert body["tickets"][0]["qr_token"] is None
    assert body["tickets"][0]["short_code"] == ""


async def test_the_pass_state_wins_once_somebody_is_through_the_door(
    client, db_session, issued
):
    issued.current_state = PassState.INSIDE
    await db_session.commit()

    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()
    assert body["tickets"][0]["state"] == "CHECKED_IN"


async def test_a_proxy_couple_shows_both_seats_on_one_account(client, db_session, event_id):
    """PROXY means nobody could claim the second seat, so the buyer holds both
    passes — two tickets, two QR codes, counted separately."""
    event = await db_session.get(Event, event_id)
    order, first = await make_ticket(
        db_session, event_id, seats=2, seq=1, partner_mode=PartnerMode.PROXY
    )
    _, second = await make_ticket(
        db_session, event_id, seats=2, seq=2, order=order, partner_mode=PartnerMode.PROXY
    )
    for ticket in (first, second):
        await issue_pass(db_session, event=event, ticket=ticket)
    await db_session.commit()

    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert len(body["tickets"]) == 2
    assert [t["seat_index"] for t in body["tickets"]] == [1, 2]
    assert all(t["seats"] == 2 for t in body["tickets"])
    # Two DIFFERENT tokens and two different codes — never one shared pass.
    assert body["tickets"][0]["qr_token"] != body["tickets"][1]["qr_token"]
    assert body["tickets"][0]["short_code"] != body["tickets"][1]["short_code"]


async def test_a_self_claim_partners_ticket_stays_off_the_buyers_account(
    client, db_session, event_id
):
    """The mirror of the PROXY case, and the reason that clause is narrow.
    A SELF_CLAIM partner signs in as themselves and holds their own pass; the
    buyer seeing it would be one person holding two identities' passes."""
    event = await db_session.get(Event, event_id)
    order, first = await make_ticket(
        db_session, event_id, seats=2, seq=1, partner_mode=PartnerMode.SELF_CLAIM
    )
    _, second = await make_ticket(
        db_session, event_id, seats=2, seq=2, order=order,
        partner_mode=PartnerMode.SELF_CLAIM,
    )
    # The partner ticket is bound to somebody else, exactly as M3 mints it.
    second.bound_net_id = "zq202"
    for ticket in (first, second):
        await issue_pass(db_session, event=event, ticket=ticket)
    await db_session.commit()

    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert len(body["tickets"]) == 1
    assert body["tickets"][0]["seat_index"] == 1


async def test_a_revoked_ticket_is_not_served_at_all(client, db_session, event_id):
    _, ticket = await make_ticket(db_session, event_id, status=TicketStatus.REVOKED)
    await db_session.commit()

    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/pass", headers=headers)).json()
    assert body["tickets"] == []


async def test_the_token_is_minted_fresh_and_never_stored(client, db_session, issued):
    """`passes.qr_object` stays NULL. A bearer credential kept in a row so it
    can be served again is a place for it to leak, and minting costs an HMAC."""
    headers = await login(client, HOLDER_NET_ID)
    first = (await client.get("/api/v1/me/pass", headers=headers)).json()
    second = (await client.get("/api/v1/me/pass", headers=headers)).json()

    assert first["tickets"][0]["qr_token"] == second["tickets"][0]["qr_token"]
    stored = await db_session.scalar(select(Pass.qr_object).where(Pass.id == issued.id))
    assert stored is None
