"""The outbox worker and its templates (M6).

M3 queued 441 "your ticket is ready" rows and nothing sent them. This is what
sends them, and the thing it must never do is send one twice.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import EmailOutbox, EmailStatus
from app.services.mailer.outbox import (
    TEMPLATE_ID_REVIEWED,
    TEMPLATE_TICKET_READY,
    QueuedEmail,
    queue_email,
)
from app.services.mailer.provider import EmailMessage
from app.services.mailer.templates import UnknownTemplate, render
from app.services.mailer.worker import (
    BACKOFF_BASE,
    MAX_ATTEMPTS,
    backoff_for,
    drain,
    drain_once,
)


class FakeMailer:
    """Records what it was asked to send. Optionally fails."""

    def __init__(self, *, fail_times: int = 0, error: str = "smtp is down") -> None:
        self.sent: list[EmailMessage] = []
        self.remaining_failures = fail_times
        self.error = error

    async def send(self, message: EmailMessage) -> None:
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError(self.error)
        self.sent.append(message)


async def enqueue(db, event_id, *, to="pk944@snu.edu.in", key="k1", payload=None):
    await queue_email(
        db,
        event_id=event_id,
        message=QueuedEmail(
            to_email=to,
            template=TEMPLATE_TICKET_READY,
            idempotency_key=key,
            payload=payload or {"name": "Aarav Sharma", "ticket_ref": "FS26-1"},
        ),
    )
    await db.commit()


@pytest_asyncio.fixture
async def queued(db_session, event_id):
    await enqueue(db_session, event_id)
    return await db_session.scalar(select(EmailOutbox))


# ── templates ────────────────────────────────────────────────────────────────
def test_the_ticket_ready_mail_links_to_the_site_and_carries_no_qr():
    """LLD-v2 §5 says "log in here", and that is the security design: a pass
    token in an inbox is one forward away from being somebody else's."""
    message = render(TEMPLATE_TICKET_READY, {"name": "Aarav Sharma", "ticket_ref": "FS26-1"})

    assert "Aarav Sharma" in message.text_body
    assert "/pass" in message.text_body
    assert message.html_body and "/pass" in message.html_body
    # No token, no QR, no image of one.
    assert "UG1." not in message.text_body
    assert "qr" not in message.text_body.lower()
    assert "<img" not in (message.html_body or "")


def test_a_partner_is_told_why_they_are_getting_this():
    """The only recipient who did not buy the ticket themselves."""
    plain = render(TEMPLATE_TICKET_READY, {"name": "Isha"})
    partner = render(TEMPLATE_TICKET_READY, {"name": "Isha", "claim_code": "abc123"})

    assert "couple ticket" in partner.text_body
    assert "couple ticket" not in plain.text_body
    # The claim code itself is a credential and never appears in the body.
    assert "abc123" not in partner.text_body
    assert "abc123" not in (partner.html_body or "")


def test_a_missing_name_does_not_produce_hi_none():
    message = render(TEMPLATE_TICKET_READY, {})
    assert message.text_body.startswith("Hi,")
    assert "None" not in message.text_body


# ── the "your ID was checked" mail ───────────────────────────────────────────
def test_an_approved_id_mail_sends_them_to_their_pass_and_carries_no_qr():
    message = render(TEMPLATE_ID_REVIEWED, {"name": "Aarav Sharma", "outcome": "APPROVED"})

    assert "approved" in message.subject.lower()
    assert message.text_body.startswith("Hi Aarav Sharma,")
    assert "/pass" in message.text_body and "/pass" in (message.html_body or "")
    assert "qr" not in message.text_body.lower()
    assert "<img" not in (message.html_body or "")


def test_a_retry_mail_says_what_to_fix_and_sends_them_to_upload_again():
    message = render(
        TEMPLATE_ID_REVIEWED,
        {"name": "Aarav", "outcome": "RETRY_REQUIRED", "note": "The card is cut off <here>"},
    )

    assert "clearer photo" in message.subject.lower()
    assert "/verify" in message.text_body
    assert "The card is cut off <here>" in message.text_body
    # The reviewer's note is escaped in the HTML part, never injected.
    assert "&lt;here&gt;" in (message.html_body or "")


def test_a_refused_mail_points_to_the_desk_not_to_another_upload():
    message = render(
        TEMPLATE_ID_REVIEWED, {"name": "Aarav", "outcome": "REJECTED", "note": None}
    )

    assert "wasn" in message.subject.lower()  # wasn't accepted
    assert "desk" in message.text_body
    assert "/verify" not in message.text_body
    assert "None" not in message.text_body


@pytest.mark.parametrize(
    ("template", "payload"),
    [
        (TEMPLATE_TICKET_READY, {"name": "Aarav"}),
        (TEMPLATE_TICKET_READY, {"name": "Isha", "claim_code": "abc"}),
        (TEMPLATE_ID_REVIEWED, {"name": "Aarav", "outcome": "APPROVED"}),
        (TEMPLATE_ID_REVIEWED, {"name": "Aarav", "outcome": "RETRY_REQUIRED"}),
        (TEMPLATE_ID_REVIEWED, {"name": "Aarav", "outcome": "REJECTED"}),
    ],
)
def test_every_email_signs_off_from_the_core_with_someone_to_contact(template, payload):
    message = render(template, payload)
    html = message.html_body or ""
    for body in (message.text_body, html):
        assert "Snuphoria x Inferno Core" in body
        assert "Pragun Kathuria" in body
        assert "Associate Secretary 1, Snuphoria" in body
        assert "9000000000" in body
        assert "pk944@snu.edu.in" in body and "snuphoria@snu.edu.in" in body
    # Tappable on a phone.
    assert 'href="tel:+919000000000"' in html
    assert 'href="mailto:pk944@snu.edu.in"' in html


@pytest.mark.parametrize(
    ("template", "payload", "heading", "button", "path"),
    [
        (TEMPLATE_TICKET_READY, {"name": "Aarav"}, "Your ticket is confirmed",
         "Sign in and upload your ID", "/pass"),
        (TEMPLATE_TICKET_READY, {"name": "Isha", "claim_code": "x"},
         "You have a seat at Fake Sangeet", "Sign in and upload your ID", "/pass"),
        (TEMPLATE_ID_REVIEWED, {"outcome": "APPROVED"}, "Your pass is ready",
         "View your pass", "/pass"),
        (TEMPLATE_ID_REVIEWED, {"outcome": "RETRY_REQUIRED", "note": "Too dark"},
         "We need a clearer photo", "Send a new photo", "/verify"),
        (TEMPLATE_ID_REVIEWED, {"outcome": "REJECTED"}, "Your ID wasn",
         "Open Fake Sangeet", "/"),
    ],
)
def test_every_email_is_a_branded_html_page_with_one_clear_button(
    template, payload, heading, button, path
):
    """The Primus IQ report's structure in Fake Sangeet's colours: a full
    document, a branded header, one heading, one button. Text only -- no
    images, which many inboxes block by default and which would let the
    wordmark vanish."""
    html = render(template, payload).html_body or ""

    assert html.lstrip().lower().startswith("<!doctype html")
    assert 'name="viewport"' in html
    assert "Fake Sangeet" in html and "band baaja no baarat" in html
    assert heading in html
    # The button: a real link, styled as one, to the right place.
    assert f'{button}</a>' in html
    assert f'href="http://localhost:8000{path}"' in html or 'href="https://' in html
    # Preview text for the inbox list, hidden in the body.
    assert "display:none" in html
    assert "<img" not in html


@pytest.mark.parametrize("payload", [{"name": "Aarav"}, {"name": "Isha", "claim_code": "x"}])
def test_the_ticket_mail_asks_them_to_finish_by_13_october(payload):
    """The first email everyone gets carries the client's deadline, in both
    parts, and no longer says "before the night", which would be a second,
    later deadline in the same message."""
    message = render(TEMPLATE_TICKET_READY, payload)
    for body in (message.text_body, message.html_body or ""):
        assert "before 13 October" in body
        assert "Before the night" not in body


def test_an_unknown_template_raises_rather_than_silently_skipping():
    with pytest.raises(UnknownTemplate):
        render("no_such_template", {})


# ── sending ──────────────────────────────────────────────────────────────────
async def test_a_queued_row_is_sent_once_and_marked_sent(db_session, event_id, queued):
    mailer = FakeMailer()

    report = await drain(db_session, mailer)

    assert (report.sent, report.failed, report.dead) == (1, 0, 0)
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "pk944@snu.edu.in"

    await db_session.refresh(queued)
    assert queued.status == EmailStatus.SENT.value
    assert queued.sent_at is not None
    assert queued.last_error is None


async def test_draining_twice_sends_nothing_the_second_time(db_session, event_id, queued):
    mailer = FakeMailer()
    await drain(db_session, mailer)
    second = await drain(db_session, mailer)

    assert second.claimed == 0
    assert len(mailer.sent) == 1  # nobody gets a second "your ticket is ready"


async def test_a_failure_retries_later_rather_than_immediately(db_session, event_id, queued):
    mailer = FakeMailer(fail_times=1)
    before = datetime.now(UTC)

    report = await drain_once(db_session, mailer)

    assert (report.sent, report.failed, report.dead) == (0, 1, 0)
    await db_session.refresh(queued)
    assert queued.status == EmailStatus.QUEUED.value
    assert queued.attempts == 1
    assert queued.last_error and "smtp is down" in queued.last_error
    # Backed off into the future — a retry loop that fires instantly is a
    # denial of service against your own mail server.
    assert queued.next_attempt_at > before + BACKOFF_BASE / 2

    # ...and it is not claimable again until then.
    assert (await drain_once(db_session, mailer)).claimed == 0


async def test_a_row_that_keeps_failing_is_given_up_on_and_says_why(
    db_session, event_id, queued
):
    mailer = FakeMailer(fail_times=MAX_ATTEMPTS)

    for _ in range(MAX_ATTEMPTS):
        # Make the row due again rather than waiting out the real backoff.
        queued.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()
        await drain_once(db_session, mailer)

    await db_session.refresh(queued)
    assert queued.status == EmailStatus.DEAD.value
    assert queued.attempts == MAX_ATTEMPTS
    assert queued.last_error  # the question an admin asks is "why didn't it go"
    # Dead is terminal: it is never claimed again.
    queued.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert (await drain_once(db_session, FakeMailer())).claimed == 0


async def test_an_unrenderable_row_dies_immediately_instead_of_retrying_six_times(
    db_session, event_id
):
    await queue_email(
        db_session,
        event_id=event_id,
        message=QueuedEmail(
            to_email="pk944@snu.edu.in",
            template="not_a_real_template",
            idempotency_key="bad-template",
        ),
    )
    await db_session.commit()

    report = await drain_once(db_session, FakeMailer())

    assert (report.failed, report.dead, report.sent) == (1, 1, 0)
    row = await db_session.scalar(select(EmailOutbox))
    assert row.status == EmailStatus.DEAD.value
    assert row.attempts == 1  # not six


async def test_one_bad_row_does_not_stop_the_others(db_session, event_id):
    await enqueue(db_session, event_id, to="a@snu.edu.in", key="good-1")
    await queue_email(
        db_session,
        event_id=event_id,
        message=QueuedEmail(
            to_email="b@snu.edu.in", template="nope", idempotency_key="bad-1"
        ),
    )
    await db_session.commit()
    await enqueue(db_session, event_id, to="c@snu.edu.in", key="good-2")

    mailer = FakeMailer()
    report = await drain(db_session, mailer)

    assert report.sent == 2
    assert report.dead == 1
    assert {m.to for m in mailer.sent} == {"a@snu.edu.in", "c@snu.edu.in"}


async def test_a_row_scheduled_for_the_future_is_left_alone(db_session, event_id, queued):
    queued.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.commit()

    assert (await drain_once(db_session, FakeMailer())).claimed == 0


def test_the_backoff_grows_and_then_stops_growing():
    first = backoff_for(1)
    second = backoff_for(2)
    assert second > first
    # Capped, so a long outage doesn't schedule a retry after the event.
    assert backoff_for(99) == backoff_for(100)
