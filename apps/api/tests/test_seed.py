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
