import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "events", "gates", "event_inventory",
    "users", "event_staff",
    "audit_log", "funnel_events",
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
