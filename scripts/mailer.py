#!/usr/bin/env python
"""Send whatever is queued in `email_outbox` (M6).

    docker compose run --rm api python /srv/scripts/mailer.py          # one pass
    docker compose run --rm api python /srv/scripts/mailer.py --watch  # keep going

Safe to run twice at once: rows are claimed with `FOR UPDATE SKIP LOCKED`, so a
second worker takes different rows rather than the same ones.

`--watch` is the closest thing to a daemon this deployment gets, and that is
deliberate — CLAUDE.md rules out Cloud Tasks and a second cloud service, so the
options are a loop in a container or a cron entry. Either is fine; both call
the same `drain`.
"""
import argparse
import asyncio
import sys

from app.core.logging import configure_logging
from app.db.session import session_factory
from app.services.mailer.provider import get_mailer
from app.services.mailer.worker import drain

#: How long `--watch` sleeps when the queue is empty. Long enough not to spin,
#: short enough that a newly ingested batch goes out within a minute.
IDLE_SLEEP_SECONDS = 30


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="keep draining forever")
    parser.add_argument("--limit", type=int, default=20, help="rows claimed per batch")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    # The API calls this at startup; a script has to do it itself. Without it
    # structlog runs unconfigured, which means WITHOUT the `redact_pii`
    # processor -- and this particular script logs one line per recipient.
    configure_logging()
    mailer = get_mailer()

    while True:
        async with session_factory() as session:
            report = await drain(session, mailer, limit=args.limit)
        # Counts only — no address, no subject. Printable in a shared terminal.
        print(  # noqa: T201
            f"sent {report.sent}, failed {report.failed}, gave up on {report.dead}"
        )
        if not args.watch:
            return 1 if report.dead else 0
        await asyncio.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
