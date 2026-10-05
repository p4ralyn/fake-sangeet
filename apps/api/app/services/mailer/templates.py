"""Render a queued outbox row into an actual message (M6).

Pure functions: a row in, a subject and two bodies out. No database, no SMTP,
no clock — so every template is testable without sending anything.

**No QR code is ever attached or embedded.** LLD-v2 §5 calls this email "your
ticket is ready, log in here", and that wording is the security design, not a
turn of phrase: a pass token is a bearer credential, and a bearer credential
sitting in an inbox forever is one forwarded message away from being somebody
else's. The email carries a link; the QR is drawn on the pass page, behind
sign-in, from a token minted at the moment it is shown.

The plain-text body is the real one. HTML is an alternative part — a university
mail client, a screen reader and a forwarded message all fall back to text, and
a "ticket is ready" mail that only works in a graphical client is a support
queue.
"""
from typing import Any

from pydantic import BaseModel

from app.core.settings import get_settings
from app.services.mailer.outbox import TEMPLATE_ID_REVIEWED, TEMPLATE_TICKET_READY


class RenderedEmail(BaseModel):
    subject: str
    text_body: str
    html_body: str | None = None


class UnknownTemplate(Exception):
    """A queued row names a template nothing can render.

    Raised rather than skipped: a row that silently never sends is worse than
    one that fails loudly into `last_error`, because nobody goes looking for
    mail that was never sent.
    """


def _site_url(path: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}{path}"


def _sign_in_url() -> str:
    """Where "log in here" points. Deep-links to the pass, so somebody who is
    already signed in lands on the thing the email is about.

    `app_base_url` is the public origin — it is what the OAuth redirect URI is
    registered against, and in the deployed setup Caddy serves the site and the
    API from it. In local dev it points at the API's own port, which does not
    serve `/pass`; that is a dev-only quirk of one variable doing both jobs,
    not something to route around here.
    """
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/pass"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ticket_ready(payload: dict[str, Any]) -> RenderedEmail:
    name = str(payload.get("name") or "").strip()
    greeting = f"Hi {name}," if name else "Hi,"
    url = _sign_in_url()
    claim_code = payload.get("claim_code")

    # A partner ticket is the only case where the recipient did not buy the
    # ticket themselves, so it is the only case that needs explaining.
    lead = (
        "Someone has booked a couple ticket for Fake Sangeet and put your name on "
        "the second seat."
        if claim_code
        else "Your ticket for Fake Sangeet is confirmed."
    )

    text = f"""{greeting}

{lead}

Before the night, sign in and upload a photo of your student ID — that is the
only thing left to do. Your pass appears once it has been checked.

{url}

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm
"""

    html = f"""<p>{_escape(greeting)}</p>
<p>{_escape(lead)}</p>
<p>Before the night, sign in and upload a photo of your student ID &mdash; that
is the only thing left to do. Your pass appears once it has been checked.</p>
<p><a href="{_escape(url)}">{_escape(url)}</a></p>
<p>Sign in with your SNU account. Anyone can open that link, but only you can
see your pass.</p>
<p><strong>Fake Sangeet</strong> &mdash; band baaja no baarat<br>
15 October, UAC Terrace, 6pm</p>
"""

    subject = (
        "Your Fake Sangeet ticket — one step left"
        if not claim_code
        else "You have a seat at Fake Sangeet — one step left"
    )
    return RenderedEmail(subject=subject, text_body=text, html_body=html)


def _id_reviewed(payload: dict[str, Any]) -> RenderedEmail:
    """The answer to an ID upload, whichever it was.

    The ID page tells people "you'll get an email when it's been looked at",
    so every outcome sends one -- and the one that most needs it is the
    clearer-photo case, because that person has something to do. Like the
    ticket mail, it carries a link and never the pass.
    """
    name = str(payload.get("name") or "").strip()
    greeting = f"Hi {name}," if name else "Hi,"
    outcome = payload.get("outcome")
    note = str(payload.get("note") or "").strip()
    said = f'They said: "{note}"' if note else ""

    if outcome == "APPROVED":
        subject = "Your ID is approved — your Fake Sangeet pass is ready"
        lines = [
            "Your student ID has been checked and approved. Your pass is ready.",
            "Sign in to see it, and keep it handy on your phone for the door:",
        ]
        url = _site_url("/pass")
    elif outcome == "RETRY_REQUIRED":
        subject = "Fake Sangeet — we need a clearer photo of your ID"
        lines = [
            "We couldn't use the photo of your student ID you sent.",
            said,
            "Sign in and send a new one — the whole card in frame, flat and in good light:",
        ]
        url = _site_url("/verify")
    else:
        subject = "Fake Sangeet — your ID wasn't accepted"
        lines = [
            "Your student ID wasn't accepted, so there is no pass on your account.",
            said,
            "If you think that's wrong, talk to the desk.",
        ]
        url = _site_url("/")

    body = "\n\n".join(line for line in lines if line)
    text = f"""{greeting}

{body}

{url}

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm
"""
    paragraphs = "\n".join(f"<p>{_escape(line)}</p>" for line in lines if line)
    html = f"""<p>{_escape(greeting)}</p>
{paragraphs}
<p><a href="{_escape(url)}">{_escape(url)}</a></p>
<p><strong>Fake Sangeet</strong> &mdash; band baaja no baarat<br>
15 October, UAC Terrace, 6pm</p>
"""
    return RenderedEmail(subject=subject, text_body=text, html_body=html)


#: Registry, not an if/else chain. A new template is a function plus one entry,
#: and `render` can tell "unknown" from "not implemented yet".
RENDERERS = {TEMPLATE_TICKET_READY: _ticket_ready, TEMPLATE_ID_REVIEWED: _id_reviewed}


def render(template: str, payload: dict[str, Any]) -> RenderedEmail:
    renderer = RENDERERS.get(template)
    if renderer is None:
        raise UnknownTemplate(f"no renderer for template {template!r}")
    return renderer(payload or {})
