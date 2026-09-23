"""Drain `email_outbox` (M6). M3 queued the rows; this sends them.

Two workers may run at once without anybody getting a second copy. The claim is
`SELECT … FOR UPDATE SKIP LOCKED`, which hands each row to exactly one worker
and lets the other walk past it instead of blocking.

`SKIP LOCKED` alone is **not** enough, and assuming it was is a mistake this
code made until a concurrency test caught it. The row lock lives only as long
as the claiming transaction, and the claim has to commit before the send (see
below) — so the moment it commits, the lock is gone and a second worker sees a
`SENDING` row it is perfectly entitled to claim. `SENDING` is in
`EMAIL_CLAIMABLE_STATUSES` on purpose: a row is not permanently spoken for
just because somebody started it.

What makes the claim hold is a **lease**: claiming pushes `next_attempt_at`
`CLAIM_LEASE` into the future, and the claim query only takes rows that are
due. Another worker therefore skips it, and a worker that dies mid-send leaves
a row that becomes claimable again by itself once the lease runs out — which a
"SENDING is off-limits" rule would have stranded forever.

The claim is **committed before the send**. That ordering is the whole design:

* commit-then-send risks a message that was sent but recorded as unsent, if the
  process dies in between — the recipient gets a second copy on the retry;
* send-then-commit risks a message recorded as sent that never left — the
  recipient gets nothing, and nobody finds out.

For "your ticket is ready", a rare duplicate is a shrug and a missing mail is
somebody standing at a door without a pass. So: claim, commit, send.

Failures back off exponentially and stop at `MAX_ATTEMPTS`, after which the row
is `DEAD` and waits for a person. A dead row keeps `last_error`, because the
question an admin will ask is "why didn't this one go".
"""
from datetime import UTC, datetime, timedelta

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import hash_pii
from app.models import EMAIL_CLAIMABLE_STATUSES, EmailOutbox, EmailStatus
from app.services.mailer.provider import EmailMessage, Mailer
from app.services.mailer.templates import UnknownTemplate, render

log = structlog.get_logger(__name__)

#: Tries before a row is given up on. Six covers a mail server that is down for
#: most of a day without hammering it.
MAX_ATTEMPTS = 6
#: First retry delay; each subsequent failure multiplies by BACKOFF_FACTOR.
BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_FACTOR = 5
BACKOFF_CAP = timedelta(hours=6)
#: How long a claimed row is invisible to other workers. Comfortably longer
#: than an SMTP send, short enough that a killed worker's rows go out on the
#: next run rather than the next day.
CLAIM_LEASE = timedelta(minutes=5)
#: The first step at which the cap is already reached. Derived from the three
#: constants above rather than written down, and used to clamp the exponent --
#: `BACKOFF_FACTOR ** attempts` on an unclamped counter overflows C int long
#: before it reaches a number anyone cares about.
_MAX_BACKOFF_STEPS = next(
    step for step in range(64) if BACKOFF_BASE * (BACKOFF_FACTOR**step) >= BACKOFF_CAP
)


class DrainReport(BaseModel):
    """Counts only — never an address, never a subject line. Safe to log."""

    claimed: int = 0
    sent: int = 0
    failed: int = 0
    dead: int = 0

    @property
    def done(self) -> bool:
        return self.claimed == 0


def backoff_for(attempts: int) -> timedelta:
    """1m, 5m, 25m, … capped. `attempts` is the count INCLUDING the one that
    just failed, so the first failure waits BACKOFF_BASE."""
    steps = min(max(0, attempts - 1), _MAX_BACKOFF_STEPS)
    return min(BACKOFF_BASE * (BACKOFF_FACTOR**steps), BACKOFF_CAP)


async def claim_batch(db: AsyncSession, *, limit: int) -> list[EmailOutbox]:
    """Take up to `limit` due rows and mark them SENDING, committed.

    `SKIP LOCKED` is what makes a second worker useful instead of merely
    patient. `EMAIL_CLAIMABLE_STATUSES` is derived from the enum, so a new
    terminal state cannot accidentally become claimable.
    """
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(EmailOutbox)
            .where(
                EmailOutbox.status.in_([s.value for s in EMAIL_CLAIMABLE_STATUSES]),
                EmailOutbox.next_attempt_at <= now,
            )
            .order_by(EmailOutbox.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    lease_until = now + CLAIM_LEASE
    for row in rows:
        row.status = EmailStatus.SENDING.value
        # The lease, not the status, is what keeps the next worker off this
        # row. See the module docstring.
        row.next_attempt_at = lease_until
    await db.commit()
    return list(rows)


async def _succeed(db: AsyncSession, row: EmailOutbox) -> None:
    row.status = EmailStatus.SENT.value
    row.sent_at = datetime.now(UTC)
    row.last_error = None
    await db.commit()


async def _fail(db: AsyncSession, row: EmailOutbox, *, error: str, retryable: bool) -> bool:
    """Record a failure. Returns True if the row is now DEAD.

    The error text is stored but never logged: a bounce message can quote the
    recipient's address back at us.
    """
    row.attempts += 1
    row.last_error = error[:500]
    if not retryable or row.attempts >= MAX_ATTEMPTS:
        row.status = EmailStatus.DEAD.value
        await db.commit()
        return True
    row.status = EmailStatus.QUEUED.value
    row.next_attempt_at = datetime.now(UTC) + backoff_for(row.attempts)
    await db.commit()
    return False


async def drain_once(db: AsyncSession, mailer: Mailer, *, limit: int = 20) -> DrainReport:
    """Claim a batch and try to send each. Never raises on a bad row."""
    report = DrainReport()
    claimed = await claim_batch(db, limit=limit)
    report.claimed = len(claimed)

    for row in claimed:
        try:
            message = render(row.template, row.payload or {})
        except UnknownTemplate as exc:
            # Not retryable: the next attempt would fail identically.
            report.dead += int(await _fail(db, row, error=str(exc), retryable=False))
            report.failed += 1
            log.error("email.unknown_template", template=row.template, id=str(row.id))
            continue

        try:
            await mailer.send(
                EmailMessage(
                    to=str(row.to_email),
                    subject=message.subject,
                    text_body=message.text_body,
                    html_body=message.html_body,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- any send failure retries
            died = await _fail(db, row, error=f"{type(exc).__name__}: {exc}", retryable=True)
            report.failed += 1
            report.dead += int(died)
            log.warning(
                "email.send_failed",
                # Hashed, per the never-log-PII guardrail: enough to correlate
                # one recipient's repeated failures, useless to anyone reading
                # the logs for addresses.
                to=hash_pii(str(row.to_email)),
                template=row.template,
                attempts=row.attempts,
                dead=died,
            )
            continue

        await _succeed(db, row)
        report.sent += 1

    if report.claimed:
        log.info("email.drained", **report.model_dump())
    return report


async def drain(db: AsyncSession, mailer: Mailer, *, limit: int = 20) -> DrainReport:
    """Keep draining until nothing is due. Returns the totals for the run."""
    total = DrainReport()
    while True:
        pass_report = await drain_once(db, mailer, limit=limit)
        total.claimed += pass_report.claimed
        total.sent += pass_report.sent
        total.failed += pass_report.failed
        total.dead += pass_report.dead
        if pass_report.done:
            return total
