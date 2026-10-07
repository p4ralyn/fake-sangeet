#!/usr/bin/env python
"""Queue the "someone sent you a pass" email for transfers made before it existed.

    docker compose run --rm api python /srv/scripts/notify_transfers.py --dry-run

Transfers made from 7 Oct 2026 onward queue this email themselves
(`app/services/passes/transfer.py`). The handful made before that told the
recipient nothing, so a new holder who was not told by the sender has no idea
they hold a pass -- and no QR until they sign in and upload an ID.

Only recipients who have **not signed in yet** are mailed by default: somebody
who has already signed in has found their pass, and an email telling them about
it is noise. `--include-signed-in` mails them too.

Safe to re-run: the email is keyed on the ticket ref, and a ticket moves once,
ever, so a second run -- or a transfer that already queued its own -- queues
nothing. Prints ticket refs only, never a NetID. `--dry-run` rolls back.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import TICKET_LIVE_STATUSES, AuditLog, Event, Ticket
from app.services.passes.transfer import (
    KIND_PARTNER_SEAT,
    KIND_TRANSFER,
    has_unsent_partner_seat,
    tell_the_recipient,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument(
        "--include-signed-in",
        action="store_true",
        help="also mail recipients who have already signed in",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; roll back")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging()
    async with session_factory() as db:
        event = await db.scalar(select(Event).where(Event.slug == args.event_slug))
        if event is None:
            print(f"no event with slug {args.event_slug!r}", file=sys.stderr)
            return 2

        tickets = (
            await db.scalars(
                select(Ticket)
                .where(
                    Ticket.event_id == event.id,
                    Ticket.transfer_count > 0,
                    Ticket.bound_net_id.is_not(None),
                    Ticket.status.in_(TICKET_LIVE_STATUSES),
                )
                .order_by(Ticket.bound_at)
            )
        ).all()

        queued = 0
        for ticket in tickets:
            if ticket.user_id is not None and not args.include_signed_in:
                print(f"  {ticket.ticket_ref}  skipped (already signed in)")
                continue
            action = await db.scalar(
                select(AuditLog.action)
                .where(AuditLog.object_id == ticket.id, AuditLog.action.like("%TRANSFERRED"))
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
            kind = KIND_PARTNER_SEAT if action == "PARTNER_SEAT_TRANSFERRED" else KIND_TRANSFER
            bundle = kind == KIND_TRANSFER and await has_unsent_partner_seat(
                db, event_id=event.id, order_id=ticket.order_id
            )
            created = await tell_the_recipient(
                db, event_id=event.id, ticket=ticket, kind=kind, brings_partner_seat=bundle
            )
            queued += int(created)
            print(f"  {ticket.ticket_ref}  {kind}  {'queued' if created else 'already queued'}")

        print(f"{queued} email(s) queued")
        if args.dry_run:
            await db.rollback()
            print("dry run; rolled back")
            return 0
        await db.commit()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
