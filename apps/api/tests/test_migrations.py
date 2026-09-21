import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

EXPECTED_TABLES = {
    "events", "gates", "event_inventory",
    "users", "event_staff",
    "audit_log", "funnel_events",
    "orders", "tickets", "passes", "scans",
}


async def test_all_core_tables_exist(db_session):
    rows = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = {row[0] for row in rows}
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"


async def test_staff_role_enum_has_exactly_the_v2_values(db_session):
    rows = await db_session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'staff_role'"
        )
    )
    assert {row[0] for row in rows} == {"OWNER", "ADMIN", "SECURITY"}


async def test_no_oversell_check_physically_refuses_an_oversell(db_session, event_id):
    """CLAUDE.md invariant: the database refuses to oversell even if app logic is wrong."""
    with pytest.raises(IntegrityError, match="no_oversell"):
        await db_session.execute(
            text(
                "UPDATE event_inventory SET seats_committed = seats_total + 1 "
                "WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        )
        await db_session.flush()


async def test_seats_committed_cannot_go_negative(db_session, event_id):
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE event_inventory SET seats_committed = -1 WHERE event_id = :event_id"),
            {"event_id": event_id},
        )
        await db_session.flush()


async def test_event_slug_is_case_insensitive(db_session, event_id):
    rows = await db_session.execute(
        text("SELECT count(*) FROM events WHERE slug = 'DRYRUN'")
    )
    assert rows.scalar() == 1


async def test_readyz_reports_both_dependencies(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


# ── M2: the access core schema ───────────────────────────────────────────


async def _insert_orphan_scan(db_session, event_id):
    """A scan with no pass — exactly the row a forged QR writes."""
    from uuid import uuid4

    scan_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO scans (id, event_id, action, result, client_scan_uuid, occurred_at) "
            "VALUES (:id, :e, 'DENIED', 'DENIED', :c, now())"
        ),
        {"id": scan_id, "e": event_id, "c": uuid4()},
    )
    await db_session.flush()
    return scan_id


async def test_scans_refuses_an_update(db_session, event_id):
    """CLAUDE.md: append-only is enforced by the database, not by convention."""
    scan_id = await _insert_orphan_scan(db_session, event_id)

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE scans SET result = 'OK' WHERE id = :id"), {"id": scan_id}
        )
        await db_session.flush()


async def test_scans_refuses_a_delete(db_session, event_id):
    scan_id = await _insert_orphan_scan(db_session, event_id)

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("DELETE FROM scans WHERE id = :id"), {"id": scan_id})
        await db_session.flush()


async def test_scans_refuses_an_update_that_would_match_nothing(db_session, event_id):
    """Statement-level, not row-level: a row-level trigger on a zero-row UPDATE
    raises nothing, and "it didn't error" would then read as "it's immutable"."""
    from uuid import uuid4

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE scans SET result = 'OK' WHERE id = :id"), {"id": uuid4()}
        )
        await db_session.flush()


async def test_one_identity_cannot_hold_two_live_tickets(db_session, event_id):
    from uuid import uuid4

    from app.models import Order, Ticket

    user_id = uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, net_id, display_name) VALUES (:id, 'dup.holder', 'Dup')"),
        {"id": user_id},
    )
    order = Order(event_id=event_id, external_id="dup-order", purchaser_email="d@snu.edu.in")
    db_session.add(order)
    await db_session.flush()
    for ref in ("FS26-AAA", "FS26-BBB"):
        db_session.add(
            Ticket(event_id=event_id, order_id=order.id, ticket_ref=ref, user_id=user_id)
        )

    with pytest.raises(IntegrityError, match="tickets_one_per_identity"):
        await db_session.flush()


async def test_a_cancelled_ticket_frees_the_identity(db_session, event_id):
    """The partial index is what makes "cancel and re-issue" possible at all."""
    from uuid import uuid4

    from app.models import Order, Ticket, TicketStatus

    user_id = uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, net_id, display_name) VALUES (:id, 're.holder', 'Re')"),
        {"id": user_id},
    )
    order = Order(event_id=event_id, external_id="re-order", purchaser_email="r@snu.edu.in")
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Ticket(
            event_id=event_id, order_id=order.id, ticket_ref="FS26-CCC",
            user_id=user_id, status=TicketStatus.CANCELLED,
        )
    )
    db_session.add(
        Ticket(event_id=event_id, order_id=order.id, ticket_ref="FS26-DDD", user_id=user_id)
    )
    await db_session.flush()  # must not raise


async def test_a_claim_code_requires_self_claim_mode(db_session, event_id):
    """A PROXY ticket has nobody to claim it, so a claim code on one is a bug."""
    from app.models import Order, Ticket
    from app.models.ticketing import PartnerMode

    order = Order(event_id=event_id, external_id="proxy-order", purchaser_email="p@snu.edu.in")
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Ticket(
            event_id=event_id, order_id=order.id, ticket_ref="FS26-EEE",
            partner_mode=PartnerMode.PROXY, claim_code="SHOULD-NOT-EXIST",
        )
    )

    with pytest.raises(IntegrityError, match="tickets_claim_code_requires_self_claim"):
        await db_session.flush()


async def test_unknown_pass_denials_never_collide_on_client_scan_uuid(db_session, event_id):
    """`UNIQUE (pass_id, client_scan_uuid)` must not stop two forged-QR
    denials being recorded: NULL pass_id never collides in Postgres."""
    from uuid import uuid4

    scan_uuid = uuid4()
    for _ in range(2):
        await db_session.execute(
            text(
                "INSERT INTO scans (id, event_id, pass_id, action, result, "
                "client_scan_uuid, occurred_at) "
                "VALUES (:id, :e, NULL, 'CHECK_IN', 'OK', :c, now())"
            ),
            {"id": uuid4(), "e": event_id, "c": scan_uuid},
        )
    # pass_id IS NULL, so the unique index does not apply -- NULLs never collide.
    await db_session.flush()


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("pass_state", {"NOT_ARRIVED", "INSIDE", "ON_BREAK", "DEPARTED", "REVOKED"}),
        (
            "scan_action",
            {"CHECK_IN", "EXIT_BREAK", "RE_ENTRY", "CHECK_OUT", "DENIED", "MANUAL_OVERRIDE"},
        ),
        ("partner_mode", {"SELF_CLAIM", "PROXY"}),
    ],
)
async def test_access_enums_match_the_spec(db_session, type_name, expected):
    rows = await db_session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :name"
        ),
        {"name": type_name},
    )
    assert {row[0] for row in rows} == expected
