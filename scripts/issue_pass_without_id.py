#!/usr/bin/env python
"""Mint passes for named people WITHOUT an ID check.

    docker compose run --rm api python /srv/scripts/issue_pass_without_id.py \
        snuphoria@snu.edu.in inferno@snu.edu.in

**This bypasses a control.** Normally a pass exists only because a human looked
at a student ID and approved it (`services/verification/service.py`), and that
is the whole point of the ID step. This skips it, so every pass it mints writes
an `ID_CHECK_BYPASSED` audit row naming the operator and the ticket. A bypass
nobody can find afterwards is indistinguishable from a bug.

It exists for the gimmick tickets used to rehearse the gate: there is no real
student ID to photograph for `snuphoria@snu.edu.in`, and the scanner cannot be
tested against a QR that was never minted.

Do not point it at a real attendee. `--dry-run` reports and rolls back.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import Event
from app.models.ticketing import TICKET_LIVE_STATUSES, TicketStatus
from app.services.audit import write_audit
from app.services.passes.issue import issue_pass
from app.services.ticketing.lookup import identity_keys, tickets_for_identities

#: Tickets this may issue for. Derived from the model's own live set rather
#: than listed out, so a status added later is covered without editing this —
#: the hardcoded-copy mistake CLAUDE.md says has been caught twice.
#:
#: CANCELLED, REJECTED and REVOKED are excluded for free, being dead: each is a
#: ticket somebody deliberately stopped, and quietly reviving one is not this
#: script's call. EXPIRED_UNCLAIMED counts as live in the model but is subtracted
#: here for the same reason — a claim window that closed is a decision too.
ISSUABLE_FROM = TICKET_LIVE_STATUSES - {TicketStatus.EXPIRED_UNCLAIMED}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identities", nargs="+", help="email addresses or NetIDs")
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument(
        "--actor",
        default="script:issue_pass_without_id",
        help="who is doing this, recorded in the audit row",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; roll back")
    return parser.parse_args(argv)


async def issue(
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

        minted = 0
        for ticket in tickets:
            if ticket.status not in ISSUABLE_FROM:
                # Never print the NetID; ticket_ref carries no PII.
                print(f"  {ticket.ticket_ref}  SKIPPED ({ticket.status.value})")
                continue

            before = ticket.status.value
            # issue_pass is idempotent: it returns the existing row, and revives
            # a revoked one by bumping `serial`. Running this twice is safe.
            issued = await issue_pass(db, event=event, ticket=ticket)
            ticket.status = TicketStatus.PASS_ISSUED
            await db.flush()

            await write_audit(
                db,
                event_id=event.id,
                actor_user_id=None,
                actor_net_id=actor,
                # NULL, not a staff role: `actor_role` is the staff_role enum
                # and no OWNER/ADMIN/SECURITY performed this. The operator is
                # named in `actor_net_id` instead.
                actor_role=None,
                action="ID_CHECK_BYPASSED",
                object_type="pass",
                object_id=issued.id,
                before={"ticket_status": before},
                after={
                    "ticket_status": ticket.status.value,
                    "short_code": issued.short_code,
                    "reason": "gate rehearsal; no real student ID exists for this account",
                },
            )
            minted += 1
            print(f"  {ticket.ticket_ref}  {before} -> PASS_ISSUED  {issued.short_code}")

        print(f"{minted} pass(es) issued without an ID check, {len(tickets)} ticket(s) seen")

        if dry_run:
            await db.rollback()
            print("dry run; rolled back")
            return 0

        await db.commit()
        print("done. the QR appears on /pass for whoever holds the ticket.")
        return 0


def main(argv: list[str]) -> int:
    configure_logging()  # installs redact_pii; a standalone script gets neither for free
    args = parse_args(argv)
    return asyncio.run(
        issue(
            args.identities,
            event_slug=args.event_slug,
            actor=args.actor,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
