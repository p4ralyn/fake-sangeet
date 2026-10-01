#!/usr/bin/env python
"""Ingest one or more ticket workbooks into the single event.

    docker compose run --rm -v ~/Downloads/ticketingestion:/data api \
        python /srv/scripts/ingest.py /data/*.xlsx

Safe to re-run: the row hash makes every row idempotent, so a second pass over
a corrected workbook mints only the rows that were not mintable before.

`--dry-run` parses and validates without writing anything, which is the right
way to see what a repaired sheet will do before it does it.
"""
import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.models import Event, EventInventory, Order
from app.services.ingestion.service import (
    IngestReport,
    ingest_source,
    row_external_id,
    row_ref_of,
    row_verdict,
)
from app.services.ingestion.sources import source_for_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbooks", nargs="+", type=Path)
    parser.add_argument("--event-slug", default="dryrun")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and validate only; roll back without writing",
    )
    return parser.parse_args(argv)


def render(report: IngestReport) -> str:
    """Counts only. A report never contains attendee data, so this is printable
    in a shared terminal and loggable."""
    line = (
        f"{report.source_key}: {report.rows_seen} rows -> "
        f"{report.orders_created} orders, {report.tickets_minted} tickets, "
        f"{report.seats_committed} seats, {report.emails_queued} emails queued; "
        f"{report.orders_updated} updated, {report.rows_flagged} flagged, "
        f"{report.rows_skipped} skipped, {report.errors_recorded} errors recorded"
    )
    if report.halted:
        line += f"\n  ** HALTED: {report.halted_reason}"
    return line


async def dry_run(session, event, source) -> IngestReport:
    """Everything `ingest_source` decides, with nothing written.

    Runs the same `row_verdict` the real path runs, so the preview cannot drift
    from the behaviour it previews.

    One thing it cannot show you: a duplicate the batch would create *itself*.
    The identity checks read committed tickets, and a dry run commits none, so
    the same NetID appearing in both workbooks previews clean and is refused on
    the real run. Deliberate — the alternative is a second, parallel view of
    "what has been minted" that could disagree with the database.
    """
    report = IngestReport(source_key=source.source_key)
    async for row in source.fetch():
        report.rows_seen += 1
        # The real path's first question, asked first here too: a row whose hash
        # already has an order is already ingested, and the real run only
        # refreshes its name and phone. Without this, a dry run over a sheet
        # that is already in reported every row as "buyer already holds a live
        # ticket" -- true, and nothing like what the real run would do.
        external_id = row_external_id(source.source_key, row)
        if await session.scalar(
            select(Order.id).where(Order.event_id == event.id, Order.external_id == external_id)
        ):
            report.orders_updated += 1
            continue
        verdict = await row_verdict(session, event_id=event.id, source=source, row=row)
        if verdict.flags:
            report.rows_flagged += 1
            for reason in verdict.flags:
                print(f"  flag  {row_ref_of(row)}: {reason}")
        if not verdict.mintable:
            report.rows_skipped += 1
            for reason in verdict.blocking:
                print(f"  SKIP  {row_ref_of(row)}: {reason}")
            continue
        report.orders_created += 1
        report.tickets_minted += row.seats
        report.seats_committed += row.seats
    return report


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    # Installs the `redact_pii` processor. A script does not get the API's
    # startup hook, and this one walks 295 rows of attendee data.
    configure_logging()
    async with session_factory() as session:
        event = await session.scalar(select(Event).where(Event.slug == args.event_slug))
        if event is None:
            print(f"no event with slug {args.event_slug!r}; run scripts/seed.py first")
            return 2

        inventory = await session.get(EventInventory, event.id)
        print(
            f"event {event.slug}: {inventory.seats_committed}/{inventory.seats_total} "
            "seats committed before ingest"
        )

        halted = False
        for path in args.workbooks:
            source = source_for_path(path)
            print(f"\n{source.describe()}")
            if args.dry_run:
                report = await dry_run(session, event, source)
            else:
                report = await ingest_source(session, event=event, source=source)
            print(render(report))
            halted = halted or report.halted

        if args.dry_run:
            await session.rollback()
            print("\ndry run: nothing written")
        else:
            await session.refresh(inventory)
            print(
                f"\n{inventory.seats_committed}/{inventory.seats_total} seats committed, "
                f"{inventory.seats_total - inventory.seats_committed} free"
            )
        return 1 if halted else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
