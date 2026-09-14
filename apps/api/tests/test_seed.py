import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

sys.path.insert(0, "/srv/scripts")


@pytest.fixture
def seed_config():
    from seed import SeedConfig

    now = datetime.now(UTC)
    return SeedConfig(
        event_slug="dryrun",
        event_name="Collab Night",
        event_venue="Main Auditorium",
        event_starts_at=now + timedelta(days=30),
        event_ends_at=now + timedelta(days=30, hours=5),
        event_seats_total=150,
        seed_owner_net_ids=["owner.test"],
        seed_admin_net_ids=["admin.test"],
        seed_security_net_ids=["security.test"],
    )


async def test_seed_creates_event_inventory_and_staff(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventInventory, EventStaff

    created = await seed(db_session, seed_config)
    assert created["events"] == 1

    event = (await db_session.execute(select(Event).where(Event.slug == "dryrun"))).scalar_one()
    assert event.config["capacity"]["total_seats"] == 150
    assert {t["code"] for t in event.config["ticket_types"]} == {"SINGLE", "COUPLE"}
    assert event.config["hold_ttl_minutes"] == 20
    assert "hard_cap" not in event.config["capacity"]  # Phase 0 decision 2.3

    inventory = (
        await db_session.execute(select(EventInventory).where(EventInventory.event_id == event.id))
    ).scalar_one()
    assert (inventory.seats_total, inventory.seats_committed) == (150, 0)

    staff_count = await db_session.scalar(select(func.count()).select_from(EventStaff))
    assert staff_count == 3


async def test_seed_is_idempotent(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventStaff

    await seed(db_session, seed_config)
    second = await seed(db_session, seed_config)

    assert second == {"events": 0, "gates": 0, "staff": 0}
    assert await db_session.scalar(select(func.count()).select_from(Event)) == 1
    assert await db_session.scalar(select(func.count()).select_from(EventStaff)) == 3


async def test_reseed_does_not_clobber_committed_seats(db_session, seed_config):
    from seed import seed

    from app.models import Event, EventInventory

    await seed(db_session, seed_config)
    event = (await db_session.execute(select(Event).where(Event.slug == "dryrun"))).scalar_one()
    inventory = (
        await db_session.execute(select(EventInventory).where(EventInventory.event_id == event.id))
    ).scalar_one()
    inventory.seats_committed = 42
    await db_session.flush()

    seed_config.event_seats_total = 170
    await seed(db_session, seed_config)
    await db_session.refresh(inventory)

    assert inventory.seats_total == 170     # capacity change applied
    assert inventory.seats_committed == 42  # live holds untouched


async def test_duplicate_netid_in_same_role_list_produces_one_row(db_session):
    """Dedupe must be by construction (F-A), not by relying on session autoflush.

    Uses a session with autoflush=False bound to the same connection/transaction as
    db_session: against the pre-fix code (select-then-add per iteration with no
    dedupe), the second pending INSERT for the same (net_id, role) pair would only
    be caught by a flush the SELECT triggers via autoflush. With autoflush off here,
    the pre-fix code either raises IntegrityError on the final flush or — if it
    happened not to — would still report staff: 2. The fix must make this pass
    regardless of session configuration.
    """
    from seed import SeedConfig, seed
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import EventStaff

    now = datetime.now(UTC)
    config = SeedConfig(
        event_slug="dryrun-dup-role",
        event_name="Collab Night",
        event_venue="Main Auditorium",
        event_starts_at=now + timedelta(days=30),
        event_ends_at=now + timedelta(days=30, hours=5),
        event_seats_total=150,
        seed_owner_net_ids=[],
        seed_admin_net_ids=["admin.dup", "admin.dup"],
        seed_security_net_ids=[],
    )

    connection = await db_session.connection()
    no_autoflush_session = AsyncSession(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        created = await seed(no_autoflush_session, config)
        # Release the savepoint so the insert is visible on the shared connection —
        # seed() only flushes, and an uncommitted nested transaction is rolled back
        # on close(). This commit does not touch the outer (db_session) transaction,
        # which conftest still rolls back at teardown for isolation.
        await no_autoflush_session.commit()
    finally:
        await no_autoflush_session.close()

    assert created["staff"] == 1

    staff_count = await db_session.scalar(
        select(func.count()).select_from(EventStaff).where(EventStaff.net_id == "admin.dup")
    )
    assert staff_count == 1


async def test_same_netid_in_two_role_lists_produces_two_rows(db_session):
    """Dedupe is on the exact (net_id, role) pair, not on net_id alone."""
    from seed import SeedConfig, seed

    from app.models import EventStaff, StaffRole

    now = datetime.now(UTC)
    config = SeedConfig(
        event_slug="dryrun-dual-role",
        event_name="Collab Night",
        event_venue="Main Auditorium",
        event_starts_at=now + timedelta(days=30),
        event_ends_at=now + timedelta(days=30, hours=5),
        event_seats_total=150,
        seed_owner_net_ids=[],
        seed_admin_net_ids=["dual.test"],
        seed_security_net_ids=["dual.test"],
    )

    created = await seed(db_session, config)
    assert created["staff"] == 2

    roles = (
        await db_session.execute(
            select(EventStaff.role)
            .where(EventStaff.net_id == "dual.test")
            .order_by(EventStaff.role)
        )
    ).scalars().all()
    assert roles == [StaffRole.ADMIN, StaffRole.SECURITY]
