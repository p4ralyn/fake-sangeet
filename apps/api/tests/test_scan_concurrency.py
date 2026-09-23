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
from app.models import (
    Event,
    EventInventory,
    Order,
    Pass,
    PassState,
    Scan,
    ScanAction,
    ScanResult,
    Ticket,
)
from app.models.ticketing import TicketStatus
from app.services.access.scans import ScanContext, confirm_decision, present_token
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


async def _check_in_once(maker, event, token):
    """One gate's whole interaction: present, then confirm CHECK_IN.

    The race moved when CHECK_IN became operator-decided. Presenting a token
    now writes nothing and releases its lock, so N simultaneous *scans* no
    longer contend — N simultaneous **confirms** do, each holding its own
    decision token. That is the real gate-day shape too: two volunteers at two
    doors, both looking at a card for the same person.
    """
    from app.core.deps import get_redis_client

    redis = get_redis_client()
    async with maker() as session:
        try:
            presented = await present_token(
                session, redis, event=event, token=token,
                context=ScanContext(client_scan_uuid=uuid4(), occurred_at=datetime.now(UTC)),
            )
        except AppError as exc:
            return exc
    if presented.status != "ACTION_REQUIRED":
        return presented  # a suppressed duplicate; nothing left to confirm
    async with maker() as session:
        try:
            return await confirm_decision(
                session, redis, event=event,
                decision_token=presented.decision_token,
                action=ScanAction.CHECK_IN,
                actor_user_id=None,
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
    """N gates confirm CHECK_IN for one person at the same instant. Exactly one
    transition lands and nobody is admitted twice."""
    maker, event, issued, token = committed_world

    outcomes = await asyncio.gather(
        *(_check_in_once(maker, event, token) for _ in range(CONCURRENT_SCANS))
    )

    applied = [
        o for o in outcomes
        if not isinstance(o, Exception) and getattr(o, "action", None) is not None
        and not o.duplicate
    ]
    assert len(applied) == 1, "more than one gate applied a transition"
    assert applied[0].action.value == "CHECK_IN"

    refreshed, ok, _ = await _counts(maker, issued.id)
    assert ok == 1, "more than one CHECK_IN was recorded"
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
        *(_check_in_once(maker, event, token) for _ in range(CONCURRENT_SCANS))
    )

    applied = [
        o for o in outcomes
        if not isinstance(o, Exception) and getattr(o, "action", None) is not None
        and not o.duplicate
    ]
    assert len(applied) == 1
    assert applied[0].action.value == "CHECK_IN"
    # Every loser is refused. Two guards do that together and this test fails
    # only when BOTH are gone: the FOR UPDATE serialises the readers, and the
    # `version` guard on the UPDATE catches anything that slips past it.
    assert len([o for o in outcomes if isinstance(o, AppError)]) == CONCURRENT_SCANS - 1

    refreshed, ok, duplicates = await _counts(maker, issued.id)
    assert (ok, duplicates) == (1, 0)
    assert refreshed.current_state is PassState.INSIDE
    assert refreshed.version == 1


async def test_simultaneous_replays_of_one_client_scan_uuid_count_once(committed_world):
    """An offline device reconnecting can fire its queue twice. The unique
    index is the backstop when the duplicate window has long passed.

    Each queued entry carries the device's own `client_scan_uuid` through its
    own decision token, so the collision happens where the write happens: in
    six simultaneous confirms, not in the presents that preceded them.
    """
    maker, event, issued, token = committed_world
    from app.core.deps import get_redis_client

    redis = get_redis_client()
    shared_uuid = uuid4()

    tokens = []
    for _ in range(6):
        async with maker() as session:
            presented = await present_token(
                session, redis, event=event, token=token,
                context=ScanContext(client_scan_uuid=shared_uuid, occurred_at=datetime.now(UTC)),
            )
        assert presented.status == "ACTION_REQUIRED"
        tokens.append(presented.decision_token)

    async def replay(decision_token):
        async with maker() as session:
            try:
                return await confirm_decision(
                    session, redis, event=event, decision_token=decision_token,
                    action=ScanAction.CHECK_IN, actor_user_id=None,
                )
            except Exception as exc:  # noqa: BLE001 -- an IntegrityError is a pass too
                return exc

    results = await asyncio.gather(*(replay(t) for t in tokens))
    assert any(not isinstance(r, Exception) for r in results)

    _, ok, _ = await _counts(maker, issued.id)
    assert ok == 1
