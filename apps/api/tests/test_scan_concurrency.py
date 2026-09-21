"""Two gates, one pass — LLD-v1 §11.3, in the spirit of addendum §A6.

Run it; don't reason about it. These tests open N real connections and fire N
real transactions at one `passes` row at the same instant. They cannot use the
shared `db_session` fixture, because that is a single connection inside one
outer transaction — which would serialise the test by accident and prove
nothing about the row lock.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import AppError
from app.models import Event, EventInventory, Order, Pass, PassState, Scan, ScanResult, Ticket
from app.models.ticketing import TicketStatus
from app.services.access.scans import ScanContext, present_token
from app.services.passes.issue import issue_pass
from app.services.passes.token import mint_pass_token

CONCURRENT_SCANS = 12


@pytest.fixture
def no_duplicate_window(monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setenv("SCAN_DUPLICATE_WINDOW_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def committed_world(db_engine):
    """A real, committed event + pass that other connections can actually see."""
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with maker() as session:
        event = Event(
            slug=f"concurrency-{uuid4().hex[:8]}",
            name="Concurrency Rehearsal",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=5),
            status="LIVE",
            config={"access": {"max_breaks": 1}},
        )
        session.add(event)
        await session.flush()
        session.add(EventInventory(event_id=event.id, seats_total=600))
        order = Order(
            event_id=event.id,
            external_id="concurrency-row",
            purchaser_email="buyer@snu.edu.in",
            purchaser_name="Aarav Sharma",
        )
        session.add(order)
        await session.flush()
        ticket = Ticket(
            event_id=event.id,
            order_id=order.id,
            ticket_ref=f"FS26-{uuid4().hex[:6].upper()}",
            status=TicketStatus.PASS_ISSUED,
        )
        session.add(ticket)
        await session.flush()
        issued = await issue_pass(session, event=event, ticket=ticket)
        await session.commit()

    token = mint_pass_token(
        event_id=event.id,
        pass_id=issued.id,
        serial=issued.serial,
        expires_at=issued.expires_at,
    )
    yield maker, event, issued, token

    async with maker() as session:
        # `scans` refuses DELETE by trigger, and ON DELETE CASCADE from `events`
        # is still a DELETE on `scans`. Lifting the trigger for the teardown is
        # itself evidence that the trigger is what makes the table immutable.
        await session.execute(text("ALTER TABLE scans DISABLE TRIGGER USER"))
        await session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event.id})
        await session.execute(text("ALTER TABLE scans ENABLE TRIGGER USER"))
        await session.commit()


async def _scan_once(maker, event, token):
    from app.core.deps import get_redis_client

    async with maker() as session:
        try:
            return await present_token(
                session,
                get_redis_client(),
                event=event,
                token=token,
                context=ScanContext(
                    client_scan_uuid=uuid4(),
                    occurred_at=datetime.now(UTC),
                ),
            )
        except AppError as exc:
            return exc


async def _counts(maker, pass_id):
    async with maker() as session:
        refreshed = await session.get(Pass, pass_id)
        ok = await session.scalar(
            select(func.count()).select_from(Scan).where(
                Scan.pass_id == pass_id, Scan.result == ScanResult.OK.value
            )
        )
        duplicates = await session.scalar(
            select(func.count()).select_from(Scan).where(
                Scan.pass_id == pass_id, Scan.result == ScanResult.DUPLICATE.value
            )
        )
        return refreshed, ok, duplicates


async def test_n_simultaneous_check_ins_record_exactly_one_transition(committed_world):
    """With §11.2 suppression on, the losers get the winner's result back."""
    maker, event, issued, token = committed_world

    outcomes = await asyncio.gather(
        *(_scan_once(maker, event, token) for _ in range(CONCURRENT_SCANS))
    )

    assert not [o for o in outcomes if isinstance(o, Exception)]
    applied = [o for o in outcomes if not o.duplicate]
    assert len(applied) == 1, "more than one gate applied a transition"
    assert applied[0].action.value == "CHECK_IN"
    assert all(o.state is PassState.INSIDE for o in outcomes)

    refreshed, ok, duplicates = await _counts(maker, issued.id)
    assert (ok, duplicates) == (1, CONCURRENT_SCANS - 1)
    assert refreshed.current_state is PassState.INSIDE
    assert refreshed.version == 1  # exactly one UPDATE landed


async def test_with_suppression_off_only_one_check_in_wins(
    committed_world, no_duplicate_window
):
    """The row lock, not the duplicate window, is what stops the double admit.

    Turning suppression off removes the safety net: every loser now reaches the
    state machine with the state the winner left behind, and CHECK_IN is no
    longer legal from INSIDE. Exactly one transition still lands.
    """
    maker, event, issued, token = committed_world

    outcomes = await asyncio.gather(
        *(_scan_once(maker, event, token) for _ in range(CONCURRENT_SCANS))
    )

    assert not [o for o in outcomes if isinstance(o, Exception)]
    done = [o for o in outcomes if o.status == "DONE"]
    prompted = [o for o in outcomes if o.status == "ACTION_REQUIRED"]
    assert len(done) == 1
    assert done[0].action.value == "CHECK_IN"
    assert len(prompted) == CONCURRENT_SCANS - 1
    # The losers are asked what to do with someone who is already inside.
    assert all({a.value for a in o.legal_actions} == {"EXIT_BREAK", "CHECK_OUT"} for o in prompted)

    refreshed, ok, duplicates = await _counts(maker, issued.id)
    assert (ok, duplicates) == (1, 0)
    assert refreshed.current_state is PassState.INSIDE
    assert refreshed.version == 1


async def test_simultaneous_replays_of_one_client_scan_uuid_count_once(committed_world):
    """An offline device reconnecting can fire its queue twice. The unique
    index is the backstop when the duplicate window has long passed."""
    maker, event, issued, token = committed_world
    from app.core.deps import get_redis_client

    shared_uuid = uuid4()

    async def replay():
        async with maker() as session:
            try:
                return await present_token(
                    session,
                    get_redis_client(),
                    event=event,
                    token=token,
                    context=ScanContext(
                        client_scan_uuid=shared_uuid, occurred_at=datetime.now(UTC)
                    ),
                )
            except Exception as exc:  # noqa: BLE001 -- an IntegrityError is a pass too
                return exc

    results = await asyncio.gather(*(replay() for _ in range(6)))
    assert any(not isinstance(r, Exception) for r in results)

    _, ok, _ = await _counts(maker, issued.id)
    assert ok == 1
