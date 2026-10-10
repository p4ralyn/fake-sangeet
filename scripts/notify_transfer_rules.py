#!/usr/bin/env python
"""Queue the "before you pass your ticket on" notice to every ticket holder.

    docker compose run --rm api python /srv/scripts/notify_transfer_rules.py --dry-run

Client, 10 Oct 2026: tell everyone that a ticket can be passed on only after
the holder has signed in with their own account and had their own ID
approved, and point them at the tutorial for the rest. The rule itself is
enforced in `app/services/passes/transfer.py`; this only announces it.

One email per person holding a live ticket, keyed on their NetID, so a re-run
queues nothing new and a couple's two people get one each. The demo account is
skipped. Greets each person by their own name or by none: the name on their
own ID registration, else the greeting already vetted for their "ticket is
ready" email when that email went to them; otherwise "Hi,".
Prints counts only, never a NetID. `--dry-run` rolls back.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.db.session import session_factory
from app.models import (
    TICKET_LIVE_STATUSES,
    EmailOutbox,
    Event,
    Order,
    Registration,
    Ticket,
)
from app.models import (
    TicketSource as SourceRecord,
)
from app.services.mailer.outbox import (
    TEMPLATE_TICKET_READY,
    TEMPLATE_TRANSFER_RULES,
    QueuedEmail,
    queue_email,
)

SKIP_SOURCES = {"DEMO"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument("--dry-run", action="store_true", help="report only; roll back")
    return parser.parse_args(argv)


async def greeting_for(db, ticket: Ticket, address: str) -> str:
    own = await db.scalar(select(Registration.full_name).where(
        Registration.ticket_id == ticket.id, Registration.superseded_at.is_(None),
        Registration.net_id == ticket.bound_net_id))
    if own:
        return own
    mail = await db.scalar(select(EmailOutbox).where(
        EmailOutbox.template == TEMPLATE_TICKET_READY, EmailOutbox.to_email == address
    ).order_by(EmailOutbox.sent_at.desc().nulls_last()).limit(1))
    return str((mail.payload or {}).get("name") or "") if mail else ""


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging()
    domain = get_settings().google_allowed_domain
    async with session_factory() as db:
        event = await db.scalar(select(Event).where(Event.slug == args.event_slug))
        if event is None:
            print(f"no event with slug {args.event_slug!r}", file=sys.stderr)
            return 2
        rows = (await db.execute(
            select(Ticket, SourceRecord.source_key)
            .join(Order, Order.id == Ticket.order_id)
            .join(SourceRecord, SourceRecord.id == Order.source_id)
            .where(Ticket.event_id == event.id, Ticket.bound_net_id.is_not(None),
                   Ticket.status.in_(TICKET_LIVE_STATUSES))
            .order_by(Ticket.bound_net_id)
        )).all()
        seen: set[str] = set()
        queued = named = skipped = 0
        for ticket, source in rows:
            net_id = ticket.bound_net_id.lower()
            if source in SKIP_SOURCES or net_id in seen:
                skipped += source in SKIP_SOURCES
                continue
            seen.add(net_id)
            address = f"{net_id}@{domain}"
            name = await greeting_for(db, ticket, address)
            named += bool(name)
            queued += int(await queue_email(db, event_id=event.id, message=QueuedEmail(
                to_email=address, template=TEMPLATE_TRANSFER_RULES,
                idempotency_key=f"{TEMPLATE_TRANSFER_RULES}:{net_id}",
                payload={"name": name})))
        print(f"{len(seen)} holders; {queued} emails queued ({named} greeted by name); "
              f"{len(seen) - queued} already had it; {skipped} demo skipped")
        if args.dry_run:
            await db.rollback()
            print("dry run; rolled back")
            return 0
        await db.commit()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
