#!/usr/bin/env python
"""Remove every trace of one or more people from the event, so a re-ingest
mints them from scratch.

    docker compose run --rm api python /srv/scripts/reset_identity.py \
        snuphoria@snu.edu.in inferno@snu.edu.in

This exists for the gimmick tickets used to rehearse the door. Ingestion is
idempotent on `ticket_ref` (derived from the source row hash), so re-running a
workbook over somebody who still has a ticket changes nothing — the only way
to replay the whole flow, ID check included, is to take the ticket away first.

It is deliberately NOT a "wipe the database" script. It touches only the rows
that belong to the identities you name, releases their seats back through the
same ledger that committed them, and leaves the other ~441 people alone.

`--dry-run` prints what it would delete and rolls back.
"""
import argparse
import asyncio
import sys

from sqlalchemy import delete, select, text

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import (
    Event,
    Order,
    Pass,
    Scan,
    Ticket,
)
from app.models.event import LedgerReason
from app.models.ingestion import EmailOutbox
from app.models.user import User
from app.models.verification import IdImage, Registration, Verification
from app.services.inventory import release_seats
from app.services.mailer.outbox import TEMPLATE_TICKET_READY

#: Deleting a ticket is the point of this script, so the seats it held go back.
#: ADMIN_RELEASE is the ledger's word for "a human took this away", which is
#: exactly what is happening.
RELEASE_REASON = LedgerReason.ADMIN_RELEASE


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "identities",
        nargs="+",
        help="email addresses or NetIDs, as they appear on the ticket",
    )
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument("--dry-run", action="store_true", help="report only; roll back")
    return parser.parse_args(argv)


def _keys(identity: str) -> list[str]:
    """Both spellings of one person.

    A ticket's `bound_net_id` holds the NetID alone (`snuphoria`) even when the
    sheet named them by address, while `users.email` holds the whole thing.
    Accepting either spelling on the command line and looking for both is the
    difference between this script working and silently reporting "no tickets
    matched" — which it did, the first time it was run.
    """
    value = identity.strip().lower()
    local, _, _domain = value.partition("@")
    return [value] if local == value else [value, local]


async def _tickets_for(db, event_id, keys: list[str]) -> list[Ticket]:
    """Every ticket reachable from a name, by either route it can be attached:
    bound to the NetID off the sheet, or owned by a signed-in user."""
    by_net_id = (
        await db.scalars(
            select(Ticket).where(Ticket.event_id == event_id, Ticket.bound_net_id.in_(keys))
        )
    ).all()

    user_ids = (
        await db.scalars(select(User.id).where(User.email.in_(keys) | User.net_id.in_(keys)))
    ).all()
    by_user = (
        await db.scalars(
            select(Ticket).where(Ticket.event_id == event_id, Ticket.user_id.in_(user_ids))
        )
        if user_ids
        else []
    )
    by_user = list(by_user)

    # A couple's second ticket hangs off the first and may carry neither key.
    found = {ticket.id: ticket for ticket in [*by_net_id, *by_user]}
    partners = (
        await db.scalars(
            select(Ticket).where(
                Ticket.event_id == event_id, Ticket.partner_of.in_(list(found))
            )
        )
        if found
        else []
    )
    for ticket in partners:
        found.setdefault(ticket.id, ticket)
    return list(found.values())


async def reset(identities: list[str], *, event_slug: str, dry_run: bool) -> int:
    keys = sorted({key for identity in identities for key in _keys(identity)})

    async with session_factory() as db:
        event = await db.scalar(select(Event).where(Event.slug == event_slug))
        if event is None:
            print(f"no event with slug {event_slug!r}", file=sys.stderr)
            return 2

        tickets = await _tickets_for(db, event.id, keys)
        if not tickets:
            print("nothing to reset; no tickets matched")
            return 0

        ticket_ids = [ticket.id for ticket in tickets]
        order_ids = {ticket.order_id for ticket in tickets}

        passes = (await db.scalars(select(Pass).where(Pass.ticket_id.in_(ticket_ids)))).all()
        pass_ids = [pass_.id for pass_ in passes]

        registration_ids = list(
            (
                await db.scalars(
                    select(Registration.id).where(Registration.ticket_id.in_(ticket_ids))
                )
            ).all()
        )

        seats = sum(1 for _ in tickets)  # one ticket is one seat, always
        print(
            f"resetting {len(tickets)} ticket(s), {len(passes)} pass(es), "
            f"{len(order_ids)} order(s), {len(registration_ids)} registration(s); "
            f"releasing {seats} seat(s)"
        )
        for ticket in tickets:
            # ticket_ref carries no PII; the NetID is what we must not print.
            print(f"  {ticket.ticket_ref}  {ticket.status.value}")

        # `scans` is append-only by database trigger, which is the correct
        # default and the reason this has to be explicit. Dropping the guard
        # for the length of one transaction is a deliberate, audited act, not a
        # convenience — nothing else in the codebase does it.
        await db.execute(text("ALTER TABLE scans DISABLE TRIGGER USER"))
        try:
            if pass_ids:
                await db.execute(delete(Scan).where(Scan.pass_id.in_(pass_ids)))
                await db.execute(delete(Pass).where(Pass.id.in_(pass_ids)))
            # The ID check hangs off the registration, not the ticket: one
            # registration, N attempts, N images.
            if registration_ids:
                await db.execute(
                    delete(Verification).where(
                        Verification.registration_id.in_(registration_ids)
                    )
                )
                await db.execute(
                    delete(IdImage).where(IdImage.registration_id.in_(registration_ids))
                )
                await db.execute(delete(Registration).where(Registration.id.in_(registration_ids)))
            # `email_outbox.idempotency_key` is UNIQUE and built from
            # `ticket_ref`, which is stable across a re-ingest. Leaving these
            # behind makes the second ingest queue zero emails — which is how
            # this was caught: the round trip minted the tickets back and sent
            # nothing.
            await db.execute(
                delete(EmailOutbox).where(
                    EmailOutbox.idempotency_key.in_(
                        [f"{TEMPLATE_TICKET_READY}:{ticket.ticket_ref}" for ticket in tickets]
                    )
                )
            )
            await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
            # Orders cascade to nothing now that their tickets are gone, and an
            # order with no tickets would show up in every count as a ghost.
            await db.execute(delete(Order).where(Order.id.in_(order_ids)))

            await release_seats(
                db, event_id=event.id, seats=seats, reason=RELEASE_REASON
            )
        finally:
            await db.execute(text("ALTER TABLE scans ENABLE TRIGGER USER"))

        if dry_run:
            await db.rollback()
            print("dry run; rolled back")
            return 0

        await db.commit()
        print("done. re-ingest the workbook to mint them again.")
        return 0


def main(argv: list[str]) -> int:
    configure_logging()  # installs redact_pii; a standalone script gets neither for free
    args = parse_args(argv)
    return asyncio.run(
        reset(args.identities, event_slug=args.event_slug, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
