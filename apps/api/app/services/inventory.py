"""Seat inventory — addendum §A1. The ONE place `seats_committed` is written.

Seats, not orders, are the unit of capacity: a COUPLE consumes 2, a SINGLE 1.
Every movement takes the row lock, re-reads inside it, decides, writes, and
records a ledger row — in that order, in one transaction, always.

Do not add a second writer. The `no_oversell` CHECK is the last line of
defence, not the mechanism: it turns a logic bug into a crashed request instead
of an oversold venue, which is a worse outcome than a clean SOLD_OUT.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventInventory, InventoryLedger, LedgerReason


class SoldOut(Exception):
    """Not enough seats remain. Raised before anything is written."""

    def __init__(self, requested: int, available: int) -> None:
        self.requested = requested
        self.available = available
        super().__init__(f"requested {requested} seats, {available} available")
async def commit_seats(
    db: AsyncSession,
    *,
    event_id: UUID,
    seats: int,
    reason: LedgerReason,
    order_id: UUID | None = None,
    intent_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> int:
    """Take `seats` against the cap under `SELECT … FOR UPDATE`.

    Returns the new `seats_committed`. Raises `SoldOut` without writing anything
    if the cap would be exceeded.

    The lock is on one row, so this briefly serialises every buyer — which is
    exactly the point. Never `count(*)` then insert: two callers read the same
    number and both commit.
    """
    if seats <= 0:
        raise ValueError("seats must be positive")

    locked = await db.execute(
        select(EventInventory).where(EventInventory.event_id == event_id).with_for_update()
    )
    inventory = locked.scalar_one_or_none()
    if inventory is None:
        raise LookupError(f"no event_inventory row for event {event_id}")

    available = inventory.seats_total - inventory.seats_committed
    if seats > available:
        raise SoldOut(requested=seats, available=available)

    inventory.seats_committed += seats
    db.add(
        InventoryLedger(
            event_id=event_id,
            order_id=order_id,
            intent_id=intent_id,
            delta=seats,
            reason=reason.value,
            seats_committed_after=inventory.seats_committed,
            actor_user_id=actor_user_id,
        )
    )
    await db.flush()
    return inventory.seats_committed


async def release_seats(
    db: AsyncSession,
    *,
    event_id: UUID,
    seats: int,
    reason: LedgerReason,
    order_id: UUID | None = None,
    intent_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> int:
    """Give seats back under the same lock. Symmetric with `commit_seats` so
    the ledger reconstructs `seats_committed` exactly by summing `delta`."""
    if seats <= 0:
        raise ValueError("seats must be positive")

    locked = await db.execute(
        select(EventInventory).where(EventInventory.event_id == event_id).with_for_update()
    )
    inventory = locked.scalar_one_or_none()
    if inventory is None:
        raise LookupError(f"no event_inventory row for event {event_id}")
    if seats > inventory.seats_committed:
        raise ValueError(
            f"cannot release {seats} seats; only {inventory.seats_committed} are committed"
        )

    inventory.seats_committed -= seats
    db.add(
        InventoryLedger(
            event_id=event_id,
            order_id=order_id,
            intent_id=intent_id,
            delta=-seats,
            reason=reason.value,
            seats_committed_after=inventory.seats_committed,
            actor_user_id=actor_user_id,
        )
    )
    await db.flush()
    return inventory.seats_committed
