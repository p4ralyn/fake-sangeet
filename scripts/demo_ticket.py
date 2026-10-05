#!/usr/bin/env python
"""Mint a demo ticket for a gimmick account, to walk the real flow at a demo.

    docker compose run --rm api python /srv/scripts/demo_ticket.py \
        --dry-run snuphoria@snu.edu.in

and when the demo is over, take it back the usual way:

    docker compose run --rm api python /srv/scripts/reset_identity.py \
        snuphoria@snu.edu.in

The ticket goes through the real ingest path — same order, same seat ledger,
same inventory lock, a ticket ref like any other — so the demo shows what an
attendee actually gets: sign in, the ID check, the pass, the gate. Three
things differ, all on purpose:

* **The identity is taken as written.** A gimmick account like `snuphoria` is
  not an `aa123` NetID, and the ingest normaliser rightly refuses it; this
  script is the one door that admits it, and only for the name you type.
* **No email is queued.** The outbox holds the one-time send to every real
  attendee, and a demo seat must never ride along in it.
* **An audit row says it was a demo** (`DEMO_TICKET_MINTED`), so a demo seat
  left behind can always be found.

It does NOT raise the seat cap. `seed.py` is the cap's only writer and the cap
equals what was sold, so a demo needs room made first: add the demo's seats
to `EVENT_SEATS_TOTAL` in `.env`, run `seed.py`, then this. After the reset,
take them back off and seed again. The script refuses, and says so, rather
than overselling.
"""
import argparse
import asyncio
import sys
from collections.abc import AsyncIterator

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import Event, EventInventory, Ticket
from app.models.ticketing import TICKET_LIVE_STATUSES
from app.services.audit import write_audit
from app.services.ingestion.normalise import DOMAIN, Identity, TicketRow
from app.services.ingestion.service import ingest_source
from app.services.ingestion.sources import TicketSource

SOURCE_KEY = "DEMO"


class DemoSource(TicketSource):
    """One row, built from the name given — never from a sheet."""

    source_type = "manual"
    identity_is_email = False
    source_key = SOURCE_KEY

    def __init__(self, row: TicketRow) -> None:
        self.row = row

    async def fetch(self) -> AsyncIterator[TicketRow]:
        yield self.row

    def describe(self) -> str:
        return "demo ticket"


def demo_row(identity: str, *, couple: bool) -> TicketRow:
    net_id = identity.strip().lower().partition("@")[0]
    buyer = Identity(net_id, f"{net_id}@{DOMAIN}", "OK", identity)
    return TicketRow(
        source=SOURCE_KEY,
        source_row=1,
        ticket_type="COUPLE" if couple else "SINGLE",
        seats=2 if couple else 1,
        buyer=buyer,
        partner=None,
        # A demo couple is PROXY: the partner seat arrives locked, which is the
        # flow worth showing.
        partner_mode="PROXY" if couple else "SELF_CLAIM",
        phone="",
        name=f"Demo ({net_id})",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity", help="the gimmick account, as a NetID or address")
    parser.add_argument("--couple", action="store_true", help="a PROXY couple, not a single")
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging()
    row = demo_row(args.identity, couple=args.couple)

    async with session_factory() as db:
        event = await db.scalar(select(Event).where(Event.slug == args.event_slug))
        if event is None:
            print(f"no event with slug {args.event_slug!r}", file=sys.stderr)
            return 2
        inventory = await db.get(EventInventory, event.id)
        room = inventory.seats_total - inventory.seats_committed
        print(f"seats {inventory.seats_committed}/{inventory.seats_total}; demo needs {row.seats}")
        if room < row.seats:
            print(
                f"no room: raise EVENT_SEATS_TOTAL to {inventory.seats_committed + row.seats} "
                "in .env, run scripts/seed.py, then run this again",
                file=sys.stderr,
            )
            return 1
        if args.dry_run:
            print(f"dry run: would mint a demo {row.ticket_type.lower()} for {row.buyer.net_id}")
            return 0

        report = await ingest_source(db, event=event, source=DemoSource(row), queue_mail=False)
        if report.tickets_minted == 0:
            print("nothing minted -- does this account already hold a ticket?", file=sys.stderr)
            return 1
        minted = await db.scalar(
            select(Ticket).where(
                Ticket.event_id == event.id,
                Ticket.bound_net_id == row.buyer.net_id,
                Ticket.status.in_(TICKET_LIVE_STATUSES),
            )
        )
        await write_audit(
            db,
            event_id=event.id,
            actor_user_id=None,
            actor_net_id="script:demo_ticket",
            actor_role=None,
            action="DEMO_TICKET_MINTED",
            object_type="ticket",
            object_id=minted.id if minted else None,
            after={"seats": row.seats, "ticket_type": row.ticket_type},
        )
        await db.commit()
        print(
            f"minted a demo {row.ticket_type.lower()} ({report.seats_committed} seat(s)), "
            "no email queued. Reset with scripts/reset_identity.py when the demo is done."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
