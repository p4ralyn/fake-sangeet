"""The ops dashboard: what it counts, who may see it, and what it alerts on.

The notification feed has no table behind it — every item is derived from rows
that already exist. So the tests that matter are the ones that create a real
condition and check the feed notices, because there is no write path to stub.
"""
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import text

from app.models import (
    EmailStatus,
    Event,
    IngestionError,
    Order,
    PassState,
    Ticket,
    TicketStatus,
    VerifyOutcome,
)
from app.schemas.ops import Severity
from app.services.mailer.outbox import TEMPLATE_TICKET_READY, QueuedEmail, queue_email
from app.services.ops import dashboard
from app.services.passes.issue import issue_pass

ADMIN_NET_ID = "zq202"
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
            "(gen_random_uuid(), :e, :sec, 'SECURITY')"
        ),
        {"e": event_id, "admin": ADMIN_NET_ID, "sec": SECURITY_NET_ID},
    )
    await db_session.commit()


async def make_pass(db_session, event_id, *, seq: int, state: PassState):
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
    assert counts.in_the_room == 2


async def test_seats_reports_the_real_cap_not_what_is_left_on_sale(db_session, event_id):
    """CLAUDE.md: `total_seats` is the venue capacity. The dashboard must show
    that number, or the gate headcount means nothing."""
    seats = await dashboard.seats(db_session, event_id=event_id)
    assert seats.total == 150  # the conftest fixture's cap
    assert seats.free == seats.total - seats.committed


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


# ── the derived feed ─────────────────────────────────────────────────────────
async def test_a_quiet_event_produces_an_empty_feed(db_session, event_id):
    """Nothing wrong means nothing to say. A feed that always has items in it
    trains people to ignore it."""
    feed = await dashboard.notifications(db_session, event_id=event_id)
    assert feed.items == []


async def test_a_dead_email_is_the_loudest_thing_on_the_board(db_session, event_id):
    await queue_email(
        db_session,
        event_id=event_id,
        message=QueuedEmail(
            to_email="nobody@snu.edu.in",
            template=TEMPLATE_TICKET_READY,
            idempotency_key="dead-1",
        ),
    )
    await db_session.execute(
        text("UPDATE email_outbox SET status = :s, attempts = 6"),
        {"s": EmailStatus.DEAD.value},
    )
    await db_session.flush()

    feed = await dashboard.notifications(db_session, event_id=event_id)

    assert feed.items[0].kind == "MAIL_DEAD"
    assert feed.items[0].severity is Severity.CRITICAL
    assert feed.items[0].count == 1
    assert feed.items[0].href == "/ops/mail"


async def test_unresolved_sheet_rows_surface_as_a_warning(db_session, event_id):
    """Until this feed existed, these four rows sat in a table nobody could
    see, and the people on them had no ticket."""
    db_session.add(
        IngestionError(
            event_id=event_id,
            row_ref="EARLY_BIRD/INFERNO:37",
            reason="buyer identity unreadable",
            raw={"buyer_raw": "???"},
        )
    )
    await db_session.flush()

    feed = await dashboard.notifications(db_session, event_id=event_id)
    item = next(item for item in feed.items if item.kind == "INGESTION_ERRORS")

    assert item.severity is Severity.WARNING
    assert item.count == 1
    assert item.href == "/ops/ingestion"


async def test_a_waiting_id_is_information_but_a_stale_one_is_a_warning(
    db_session, event_id
):
    from app.models import IdImage, Registration, Verification

    registration = Registration(
        event_id=event_id,
        ticket_id=(
            await make_pass(db_session, event_id, seq=9, state=PassState.NOT_ARRIVED)
        ).ticket_id,
        user_id=None,
        full_name="Aarav",
        net_id=HOLDER,
        email=f"{HOLDER}@snu.edu.in",
        consent_version="v1",
        consented_at=datetime.now(UTC),
    )
    # user_id is NOT NULL, so borrow one.
    await db_session.execute(
        text("INSERT INTO users (id, net_id, email) VALUES (gen_random_uuid(), :n, :e)"),
        {"n": HOLDER, "e": f"{HOLDER}@snu.edu.in"},
    )
    user_id = await db_session.scalar(
        text("SELECT id FROM users WHERE net_id = :n"), {"n": HOLDER}
    )
    registration.user_id = user_id
    db_session.add(registration)
    await db_session.flush()
    image = IdImage(
        event_id=event_id,
        registration_id=registration.id,
        object_key="k",
        content_type="image/jpeg",
        bytes=10,
        sha256="abc",
    )
    db_session.add(image)
    await db_session.flush()
    verification = Verification(
        event_id=event_id,
        registration_id=registration.id,
        id_image_id=image.id,
        attempt_no=1,
        outcome=VerifyOutcome.MANUAL_REVIEW,
    )
    db_session.add(verification)
    await db_session.flush()

    fresh = await dashboard.notifications(db_session, event_id=event_id)
    assert next(i for i in fresh.items if i.kind == "IDS_WAITING").severity is Severity.INFO

    # Age it past the threshold.
    await db_session.execute(
        text("UPDATE verifications SET created_at = :t"),
        {"t": datetime.now(UTC) - timedelta(minutes=dashboard.IDS_STALE_MINUTES + 5)},
    )
    await db_session.flush()

    stale = await dashboard.notifications(db_session, event_id=event_id)
    item = next(i for i in stale.items if i.kind == "IDS_STALE")
    assert item.severity is Severity.WARNING
    assert "minutes" in item.title


async def test_the_feed_is_ordered_worst_first(db_session, event_id):
    db_session.add(
        IngestionError(event_id=event_id, row_ref="r", reason="broken", raw={})
    )
    await queue_email(
        db_session,
        event_id=event_id,
        message=QueuedEmail(
            to_email="a@snu.edu.in", template=TEMPLATE_TICKET_READY, idempotency_key="k1"
        ),
    )
    await queue_email(
        db_session,
        event_id=event_id,
        message=QueuedEmail(
            to_email="b@snu.edu.in", template=TEMPLATE_TICKET_READY, idempotency_key="k2"
        ),
    )
    await db_session.execute(
        text("UPDATE email_outbox SET status = :s WHERE idempotency_key = 'k2'"),
        {"s": EmailStatus.DEAD.value},
    )
    await db_session.flush()

    feed = await dashboard.notifications(db_session, event_id=event_id)
    severities = [item.severity for item in feed.items]

    assert severities[0] is Severity.CRITICAL
    order = ["CRITICAL", "WARNING", "INFO"]
    assert severities == sorted(severities, key=lambda s: order.index(s.value))


# ── the trays ────────────────────────────────────────────────────────────────
async def test_the_ingestion_tray_shows_the_raw_row_so_it_can_be_fixed(
    db_session, event_id
):
    db_session.add(
        IngestionError(
            event_id=event_id,
            row_ref="PHASE1:97",
            reason="buyer identity unreadable",
            raw={"buyer_raw": "Rohan (no id)", "name": "Rohan"},
        )
    )
    await db_session.flush()

    tray = await dashboard.ingestion_tray(db_session, event_id=event_id)

    assert tray.unresolved == 1
    assert tray.items[0].row_ref == "PHASE1:97"
    # The whole point: an admin has to read this to repair the sheet.
    assert tray.items[0].raw["buyer_raw"] == "Rohan (no id)"


async def test_a_resolved_row_leaves_the_tray(db_session, event_id):
    db_session.add(
        IngestionError(
            event_id=event_id, row_ref="r", reason="broken", raw={},
            resolved_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    tray = await dashboard.ingestion_tray(db_session, event_id=event_id)
    assert tray.unresolved == 0 and tray.items == []


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
    for path in ("summary", "notifications", "ingestion-errors", "mail"):
        assert (await client.get(f"/api/v1/ops/{path}", headers=headers)).status_code == 403


async def test_security_cannot_see_the_dashboard(client, staff, event_id):
    """A gate volunteer's tool is /scan. Their phone is often borrowed, and
    their scan response is narrowed on purpose — handing that same device the
    whole queue would undo the narrowing."""
    headers = await login(client, SECURITY_NET_ID)
    for path in ("summary", "notifications", "ingestion-errors", "mail"):
        assert (await client.get(f"/api/v1/ops/{path}", headers=headers)).status_code == 403


async def test_an_admin_sees_every_panel(client, staff, event_id):
    headers = await login(client, ADMIN_NET_ID)

    summary = await client.get("/api/v1/ops/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["seats"]["total"] == 150
    assert summary.json()["scans_window_minutes"] == dashboard.RECENT_WINDOW_MINUTES

    for path in ("notifications", "ingestion-errors", "mail"):
        assert (await client.get(f"/api/v1/ops/{path}", headers=headers)).status_code == 200
