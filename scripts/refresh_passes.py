#!/usr/bin/env python
"""Refresh the QR codes of named people: new tokens, clean slate at the door.

    docker compose run --rm api python /srv/scripts/refresh_passes.py \
        snuphoria@snu.edu.in inferno@snu.edu.in

Two things happen to each pass, and both matter:

* **`serial` is bumped**, so every token ever minted against the old serial
  stops verifying (§10.1). A QR already on a screen, screenshotted or forwarded
  is dead the moment this runs. This is the "refresh" — the QR is minted per
  request from the pass and its serial, never stored, so there is nothing else
  to rotate.
* **The pass winds back to NOT_ARRIVED**, breaks cleared. A rehearsal that ends
  in DEPARTED cannot be run again — check-out is binding by design — so without
  this the second dry run has nothing to scan.

**`scans` is not touched.** It is append-only by database trigger and it is the
record of what actually happened at the door; a refresh rewrites the pass, not
history. The state machine reads `passes.current_state`, not the scan log, so
the old rows are inert.

The short code is kept: it is printed on the paper fallback list against this
ticket, and changing it would invalidate a sheet somebody is holding.

`--dry-run` reports and rolls back.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import Event, Pass
from app.services.audit import write_audit
from app.services.passes.issue import refresh_in_place
from app.services.ticketing.lookup import identity_keys, tickets_for_identities


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identities", nargs="+", help="email addresses or NetIDs")
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument(
        "--actor",
        default="script:refresh_passes",
        help="who is doing this, recorded in the audit row",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; roll back")
    return parser.parse_args(argv)


async def refresh(
    identities: list[str], *, event_slug: str, actor: str, dry_run: bool
) -> int:
    keys = identity_keys(identities)

    async with session_factory() as db:
        event = await db.scalar(select(Event).where(Event.slug == event_slug))
        if event is None:
            print(f"no event with slug {event_slug!r}", file=sys.stderr)
            return 2

        tickets = await tickets_for_identities(db, event_id=event.id, keys=keys)
        if not tickets:
            print("nothing to do; no tickets matched")
            return 0

        refreshed = 0
        for ticket in tickets:
            pass_ = await db.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
            if pass_ is None:
                # Never print the NetID; ticket_ref carries no PII.
                print(f"  {ticket.ticket_ref}  SKIPPED (no pass to refresh)")
                continue

            before = {
                "serial": pass_.serial,
                "state": pass_.current_state.value,
                "breaks_used": pass_.breaks_used,
            }
            refresh_in_place(pass_)
            await db.flush()

            await write_audit(
                db,
                event_id=event.id,
                actor_user_id=None,
                # NULL role: `actor_role` is the staff_role enum and no
                # OWNER/ADMIN/SECURITY performed this. The operator is named
                # in `actor_net_id` instead.
                actor_role=None,
                actor_net_id=actor,
                action="PASS_REFRESHED",
                object_type="pass",
                object_id=pass_.id,
                before=before,
                after={
                    "serial": pass_.serial,
                    "state": pass_.current_state.value,
                    "breaks_used": pass_.breaks_used,
                },
            )
            refreshed += 1
            print(
                f"  {ticket.ticket_ref}  {pass_.short_code}  "
                f"serial {before['serial']} -> {pass_.serial}, "
                f"{before['state']} -> {pass_.current_state.value}"
            )

        print(f"{refreshed} pass(es) refreshed; every older QR for them is now dead")

        if dry_run:
            await db.rollback()
            print("dry run; rolled back")
            return 0

        await db.commit()
        print("done. reload /pass to get the new QR.")
        return 0


def main(argv: list[str]) -> int:
    configure_logging()  # installs redact_pii; a standalone script gets neither for free
    args = parse_args(argv)
    return asyncio.run(
        refresh(
            args.identities,
            event_slug=args.event_slug,
            actor=args.actor,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
