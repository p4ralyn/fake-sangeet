"""The ops dashboard: what it counts, who may see it, and what it alerts on.

The notification feed has no table behind it — every item is derived from rows
that already exist. So the tests that matter are the ones that create a real
condition and check the feed notices, because there is no write path to stub.
"""
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import text

from app.api.v1.routes import ops as ops_routes
from app.models import (
    EmailStatus,
    Event,
    GuestTag,
    Order,
    PassState,
    Ticket,
    TicketStatus,
)
from app.services.mailer.outbox import TEMPLATE_TICKET_READY, QueuedEmail, queue_email
from app.services.ops import dashboard
from app.services.passes.issue import issue_pass

#: Every path the ops router actually serves, derived from the router rather
#: than typed out. A hardcoded copy is how an endpoint gets added without the
#: role tests below ever seeing it — CLAUDE.md has caught that twice already.
OPS_PATHS = sorted(
    route.path.removeprefix("/api/v1/ops/")
    for route in ops_routes.router.routes
)
# Without this the role tests below would pass vacuously if the router were
# ever empty: `for path in []` asserts nothing and still goes green. That is
# the exact shape of the five tests this project has already had pass for the
# wrong reason.
assert OPS_PATHS, "the ops router serves nothing; the role tests below prove nothing"

#: Panels that were removed. Listed so the role tests cannot quietly stop
#: covering an endpoint that came back without a guard.
REMOVED_OPS_PATHS = ("notifications", "ingestion-errors")

ADMIN_NET_ID = "zq202"
OWNER_NET_ID = "pk944owner"
SECURITY_NET_ID = "zq203"
HOLDER = "pk944"


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
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, :admin, 'ADMIN'), "
            "(gen_random_uuid(), :e, :owner, 'OWNER'), "
            "(gen_random_uuid(), :e, :sec, 'SECURITY')"
        ),
        {
            "e": event_id,
            "admin": ADMIN_NET_ID,
            "owner": OWNER_NET_ID,
            "sec": SECURITY_NET_ID,
        },
    )
    await db_session.commit()


async def make_pass(
    db_session, event_id, *, seq: int, state: PassState, tag: GuestTag = GuestTag.BAARATI
):
    event = await db_session.get(Event, event_id)
    order = Order(
        event_id=event_id,
        external_id=f"ops-{seq}",
        purchaser_email=f"p{seq}@snu.edu.in",
        purchaser_name=f"Person {seq}",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    ticket = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref=f"FS26-OPS-{seq}",
        status=TicketStatus.PASS_ISSUED,
        bound_net_id=f"op{seq:03d}",
        guest_tag=tag,
    )
    db_session.add(ticket)
    await db_session.flush()
    issued = await issue_pass(db_session, event=event, ticket=ticket)
    issued.current_state = state
    await db_session.flush()
    return issued


# ── the numbers ──────────────────────────────────────────────────────────────
async def test_occupancy_counts_every_pass_state(db_session, event_id):
    await make_pass(db_session, event_id, seq=1, state=PassState.INSIDE)
    await make_pass(db_session, event_id, seq=2, state=PassState.INSIDE)
    await make_pass(db_session, event_id, seq=3, state=PassState.ON_BREAK)
    await make_pass(db_session, event_id, seq=4, state=PassState.DEPARTED)
    await make_pass(db_session, event_id, seq=5, state=PassState.NOT_ARRIVED)

    counts = await dashboard.occupancy(db_session, event_id=event_id)

    assert counts.inside == 2
    assert counts.on_break == 1
    assert counts.departed == 1
    assert counts.not_arrived == 1


async def test_the_summary_splits_tickets_and_presence_by_guest_tag(db_session, event_id):
    """14 Oct 2026: both sides always listed, even at zero, and counted
    separately for tickets held, inside and on a break."""
    await make_pass(db_session, event_id, seq=1, state=PassState.INSIDE)
    await make_pass(db_session, event_id, seq=2, state=PassState.NOT_ARRIVED)
    await make_pass(db_session, event_id, seq=3, state=PassState.ON_BREAK, tag=GuestTag.GHARWALE)

    sides = {row.tag: row for row in await dashboard.by_tag(db_session, event_id=event_id)}

    assert list(sides) == [GuestTag.BAARATI, GuestTag.GHARWALE]
    assert (sides[GuestTag.BAARATI].tickets, sides[GuestTag.BAARATI].inside) == (2, 1)
    assert (sides[GuestTag.GHARWALE].tickets, sides[GuestTag.GHARWALE].on_break) == (1, 1)
    assert sides[GuestTag.GHARWALE].inside == 0


async def test_seats_reports_the_real_cap_not_what_is_left_on_sale(db_session, event_id):
    """CLAUDE.md: `total_seats` is the venue capacity. The dashboard must show
    that number, or the gate headcount means nothing."""
    seats = await dashboard.seats(db_session, event_id=event_id)
    assert seats.total == 150  # the conftest fixture's cap
    assert seats.committed == 0


async def test_the_summary_counts_recent_scans_and_refusals(db_session, event_id):
    issued = await make_pass(db_session, event_id, seq=1, state=PassState.INSIDE)
    now = datetime.now(UTC)
    await db_session.execute(
        text(
            "INSERT INTO scans (id, event_id, pass_id, action, result, occurred_at, "
            "client_scan_uuid) VALUES "
            "(gen_random_uuid(), :e, :p, 'CHECK_IN', 'OK', :recent, gen_random_uuid()), "
            "(gen_random_uuid(), :e, :p, 'DENIED', 'DENIED', :recent, gen_random_uuid()), "
            "(gen_random_uuid(), :e, :p, 'CHECK_IN', 'OK', :old, gen_random_uuid())"
        ),
        {
            "e": event_id,
            "p": issued.id,
            "recent": now - timedelta(minutes=2),
            "old": now - timedelta(hours=3),
        },
    )
    await db_session.flush()

    facts = await dashboard.summary(db_session, event_id=event_id)

    assert facts.scans_recent == 2  # the three-hour-old one is outside the window
    assert facts.refusals_recent == 1


async def test_mail_health_separates_a_queue_from_a_problem(db_session, event_id):
    """A row that has never been tried is a queue, not a problem. Listing it
    as one buries the rows that actually failed."""
    for key in ("fresh", "retrying", "dead"):
        await queue_email(
            db_session,
            event_id=event_id,
            message=QueuedEmail(
                to_email=f"{key}@snu.edu.in",
                template=TEMPLATE_TICKET_READY,
                idempotency_key=key,
            ),
        )
    await db_session.execute(
        text(
            "UPDATE email_outbox SET attempts = 2, last_error = 'smtp down' "
            "WHERE idempotency_key = 'retrying'"
        )
    )
    await db_session.execute(
        text(
            "UPDATE email_outbox SET status = :s, attempts = 6, last_error = 'gave up' "
            "WHERE idempotency_key = 'dead'"
        ),
        {"s": EmailStatus.DEAD.value},
    )
    await db_session.flush()

    health = await dashboard.mail_health(db_session, event_id=event_id)

    assert health.dead == 1
    assert health.queued == 2
    addresses = {problem.to_email for problem in health.problems}
    assert addresses == {"retrying@snu.edu.in", "dead@snu.edu.in"}
    assert all(problem.last_error for problem in health.problems)


# ── who may look ─────────────────────────────────────────────────────────────
async def test_the_dashboard_needs_a_session(client, event_id):
    assert (await client.get("/api/v1/ops/summary")).status_code == 401


async def test_a_participant_cannot_see_the_dashboard(client, event_id):
    headers = await login(client, HOLDER)
    for path in OPS_PATHS:
        assert (await client.get(f"/api/v1/ops/{path}", headers=headers)).status_code == 403


#: What SECURITY may read here: counts, and nothing that names anybody.
GATE_READABLE = frozenset({"summary"})
assert GATE_READABLE <= set(OPS_PATHS), "GATE_READABLE names a path the router does not serve"


async def test_security_sees_the_counts_and_nothing_that_names_anybody(
    client, staff, event_id
):
    """The line is per-attendee data, not the area.

    A volunteer needs to know the room is filling and the door is alive, so
    `/summary` is open to them. `/mail` names people who never got their
    ticket, which is the 600-attendee state §11.5 keeps off a borrowed phone.
    """
    headers = await login(client, SECURITY_NET_ID)
    for path in OPS_PATHS:
        response = await client.get(f"/api/v1/ops/{path}", headers=headers)
        expected = 200 if path in GATE_READABLE else 403
        assert response.status_code == expected, f"{path}: {response.status_code}"

    # Named explicitly as well as by the loop: if `/mail` ever stops naming
    # people the loop would quietly pass either way, and this is the assertion
    # that should have to be deleted on purpose.
    assert (await client.get("/api/v1/ops/mail", headers=headers)).status_code == 403


async def test_an_admin_sees_every_panel(client, staff, event_id):
    headers = await login(client, ADMIN_NET_ID)

    summary = await client.get("/api/v1/ops/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["seats"]["total"] == 150
    assert summary.json()["scans_window_minutes"] == dashboard.RECENT_WINDOW_MINUTES

    assert (await client.get("/api/v1/ops/mail", headers=headers)).status_code == 200


async def test_an_owner_sees_every_panel_too(client, staff, event_id):
    """The role ladder, end to end: OWNER and ADMIN in, SECURITY and everybody
    else out. Asserted rather than assumed, because "the dashboard is empty"
    and "you are not allowed in" look identical from outside."""
    headers = await login(client, OWNER_NET_ID)

    for path in OPS_PATHS:
        response = await client.get(f"/api/v1/ops/{path}", headers=headers)
        assert response.status_code == 200, path


async def test_the_removed_panels_are_gone_not_merely_unlinked(client, staff, event_id):
    """Alerts and the sheet-row tray were cut because every number they showed
    is on Tonight. An endpoint left serving after its screen goes is a thing
    nobody tests and nobody guards, so assert it is actually gone — for an
    OWNER, the one role that could otherwise reach it."""
    headers = await login(client, OWNER_NET_ID)
    for path in REMOVED_OPS_PATHS:
        response = await client.get(f"/api/v1/ops/{path}", headers=headers)
        assert response.status_code == 404, path
        assert path not in OPS_PATHS
