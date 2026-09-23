#!/usr/bin/env python
"""Mint one sheet-shaped ticket for a NetID, for walking the M4 flow by hand.

    docker compose run --rm api python /srv/scripts/demo_ticket.py pk944

Exactly what M3's ingester produces for a single row — an order, one ticket
bound to a NetID with nobody signed in behind it, and the seat committed
through the inventory service. Idempotent on the same NetID.

A development convenience, not part of any milestone. `scripts/ingest.py` is
how real tickets arrive.
"""
import asyncio
import sys

from sqlalchemy import select

from app.db.session import session_factory
from app.models import Event, LedgerReason, Order, Ticket, TicketStatus
from app.services.inventory import commit_seats
from app.services.mailer.outbox import TEMPLATE_TICKET_READY, QueuedEmail, queue_email


async def main(net_id: str) -> int:
    async with session_factory() as session:
        event = await session.scalar(select(Event).where(Event.slug == "dryrun"))
        if event is None:
            print("no event; run scripts/seed.py first")  # noqa: T201
            return 2

        existing = await session.scalar(
            select(Ticket).where(Ticket.event_id == event.id, Ticket.bound_net_id == net_id)
        )
        if existing is not None:
            print(f"{net_id} already holds ticket {existing.ticket_ref} ({existing.status})")  # noqa: T201
            return 0

        order = Order(
            event_id=event.id,
            external_id=f"demo-{net_id}",
            purchaser_email=f"{net_id}@snu.edu.in",
            purchaser_name=net_id.upper(),
            quantity=1,
        )
        session.add(order)
        await session.flush()

        ticket = Ticket(
            event_id=event.id,
            order_id=order.id,
            ticket_ref=f"FS26-DEMO-{net_id.upper()}",
            status=TicketStatus.PURCHASED,
            bound_net_id=net_id,
        )
        session.add(ticket)
        await session.flush()
        await commit_seats(
            session, event_id=event.id, seats=1, reason=LedgerReason.INGEST, order_id=order.id
        )
        # Queued exactly as the ingester does, so `scripts/mailer.py` has
        # something real to send and the whole path can be walked by hand.
        await queue_email(
            session,
            event_id=event.id,
            message=QueuedEmail(
                to_email=f"{net_id}@snu.edu.in",
                template=TEMPLATE_TICKET_READY,
                idempotency_key=f"{TEMPLATE_TICKET_READY}:{ticket.ticket_ref}",
                payload={"ticket_ref": ticket.ticket_ref, "name": net_id.upper()},
            ),
        )
        await session.commit()
        print(f"{net_id} now holds {ticket.ticket_ref}, mail queued")  # noqa: T201
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "pk944")))
