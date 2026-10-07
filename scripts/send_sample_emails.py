#!/usr/bin/env python
"""Send one of every email the app can send, to ONE address, to check them.

    docker compose run --rm api python /srv/scripts/send_sample_emails.py \
        --to pk944@snu.edu.in

Five messages, each subject marked [SAMPLE]: the ticket-ready email (buyer and
partner versions) and the ID-review email (approved, clearer photo, refused),
rendered by the real templates with made-up details.

It goes straight through the configured SMTP server and **never touches the
outbox**, so it cannot release, resend or reorder the mail held for real
attendees, and it works with the `mailer` service stopped. Where it lands is
whatever `SMTP_HOST` says: locally that is mailpit (http://localhost:8025),
which keeps everything and delivers nothing; real delivery needs real SMTP
settings. `--dry-run` prints the subjects and sends nothing.
"""
import argparse
import asyncio
import sys

from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.services.mailer.outbox import (
    TEMPLATE_ID_REVIEWED,
    TEMPLATE_PASS_RECEIVED,
    TEMPLATE_TICKET_READY,
)
from app.services.mailer.provider import EmailMessage, get_mailer
from app.services.mailer.templates import render

SAMPLES = [
    ("ticket ready, buyer", TEMPLATE_TICKET_READY, {"name": "Aarav Sharma"}),
    ("ticket ready, partner", TEMPLATE_TICKET_READY, {"name": "Priya Kapoor", "claim_code": "x"}),
    ("ID approved", TEMPLATE_ID_REVIEWED, {"name": "Aarav Sharma", "outcome": "APPROVED"}),
    (
        "ID needs a clearer photo",
        TEMPLATE_ID_REVIEWED,
        {"name": "Aarav Sharma", "outcome": "RETRY_REQUIRED",
         "note": "The bottom of the card is cut off."},
    ),
    (
        "ID refused",
        TEMPLATE_ID_REVIEWED,
        {"name": "Aarav Sharma", "outcome": "REJECTED",
         "note": "This is not an SNU student ID."},
    ),
    ("pass transferred to you", TEMPLATE_PASS_RECEIVED, {"kind": "transfer"}),
    (
        "pass transferred to you, couple",
        TEMPLATE_PASS_RECEIVED,
        {"kind": "transfer", "brings_partner_seat": True},
    ),
    ("partner seat sent to you", TEMPLATE_PASS_RECEIVED, {"kind": "partner_seat"}),
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="the one address that receives all five")
    parser.add_argument("--dry-run", action="store_true", help="list them; send nothing")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging()
    settings = get_settings()
    print(f"from {settings.smtp_from} via {settings.smtp_host}:{settings.smtp_port} to {args.to}")
    mailer = get_mailer()
    for label, template, payload in SAMPLES:
        rendered = render(template, payload)
        subject = f"[SAMPLE] {rendered.subject}"
        if args.dry_run:
            print(f"  would send: {label}: {subject}")
            continue
        await mailer.send(
            EmailMessage(
                to=args.to,
                subject=subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
            )
        )
        print(f"  sent: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
