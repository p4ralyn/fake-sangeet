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


# Who to ask when something goes wrong. One block, appended to every email,
# so a person stuck at any step has a name and a number -- and so a change of
# contact is one edit here, not one per template.
CONTACT_NAME = "Pragun Kathuria"
CONTACT_ROLE = "Associate Secretary 1, Snuphoria"
CONTACT_PHONE = "9000000000"
CONTACT_EMAILS = ("pk944@snu.edu.in", "snuphoria@snu.edu.in")
SIGN_OFF = "Snuphoria x Inferno Core"

#: The client's date for having a pass ready (5 Oct 2026). Asked for in the
#: first email; nothing in the app enforces it -- uploads stay open after it.
SETUP_DEADLINE = "13 October"
DEADLINE_LINE = f"Please finish setting up your pass before {SETUP_DEADLINE}."


def _footer_text() -> str:
    first, second = CONTACT_EMAILS
    return f"""Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm

— {SIGN_OFF}

Facing any issues? Reach out to {CONTACT_NAME} ({CONTACT_ROLE})
on {CONTACT_PHONE}, or at {first} or {second}.
"""


# The poster's palette, for the HTML part. Email clients ignore stylesheets
# and web fonts unevenly, so everything is inline, tables do the layout, and
# the type falls back to Georgia and Arial -- the same approach as the Primus
# IQ daily report this layout follows.
OCHRE = "#E7A93A"
SCARLET = "#C8102E"
MARIGOLD = "#FFC61A"
INK = "#4A0A14"
PAPER = "#FFF3DC"
PANEL = "#FBE6BF"
MUTED = "#6B4A3E"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "Arial, Helvetica, sans-serif"
SPROCKETS = 20  # holes per film-strip row; percentage cells, so it fits a phone


def _sprocket_row() -> str:
    """A row of the reel's sprocket holes, drawn with table cells -- the one
    piece of the site's film strip that survives every email client."""
    hole = (
        f'<td width="{100 // SPROCKETS}%" align="center" style="padding:6px 0;">'
        f'<div style="width:9px;height:6px;background:{PAPER};border-radius:2px;'
        f'font-size:0;line-height:0;">&nbsp;</div></td>'
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"><tr>{hole * SPROCKETS}</tr></table>'
    )


def _footer_html() -> str:
    first, second = CONTACT_EMAILS
    return f"""<p style="margin:0 0 6px;font-family:{SERIF};font-size:15px;color:{INK};">
<strong>Fake Sangeet</strong> &mdash; band baaja no baarat<br>
15 October, UAC Terrace, 6pm</p>
<p style="margin:0 0 14px;font-family:{SERIF};font-style:italic;font-size:15px;color:{SCARLET};">
&mdash; {_escape(SIGN_OFF)}</p>
<p style="margin:0;font-size:13px;line-height:1.6;color:{MUTED};">
Facing any issues? Reach out to {_escape(CONTACT_NAME)} ({_escape(CONTACT_ROLE)}) on
<a href="tel:+91{CONTACT_PHONE}" style="color:{SCARLET};">{CONTACT_PHONE}</a>, or at
<a href="mailto:{first}" style="color:{SCARLET};">{first}</a> or
<a href="mailto:{second}" style="color:{SCARLET};">{second}</a>.</p>"""


def _layout(
    *,
    subject: str,
    preheader: str,
    heading: str,
    paragraphs: list[str],
    button_label: str,
    button_url: str,
    panel_label: str = "",
    panel_text: str = "",
    after_panel: tuple[str, ...] = (),
) -> str:
    """Every HTML email, one shape: the reel as a header band, one heading,
    the words, at most one tinted panel, one button, the footer.

    Every value passed in is plain text and is escaped here, so a reviewer's
    note or a name from a spreadsheet can never become markup.
    """
    body = "\n".join(
        f'<p style="margin:0 0 16px;">{_escape(line)}</p>' for line in paragraphs if line
    )
    panel = (
        f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="margin:4px 0 22px;background:{PANEL};border-left:4px solid {SCARLET};border-radius:4px;">
<tr><td style="padding:14px 18px;font-size:15px;line-height:1.5;color:{INK};">
<div style="font-size:12px;font-weight:bold;color:{MUTED};padding-bottom:4px;">{_escape(panel_label)}</div>
{_escape(panel_text)}</td></tr></table>"""
        if panel_text
        else ""
    )
    after = "\n".join(
        f'<p style="margin:0 0 16px;">{_escape(line)}</p>' for line in after_panel if line
    )
    url = _escape(button_url)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{_escape(subject)}</title>
<style>
  @media only screen and (max-width: 600px) {{
    .fs-page {{ padding: 16px 8px !important; }}
    .fs-body {{ padding: 26px 20px !important; }}
    .fs-foot {{ padding: 20px 20px 24px !important; }}
    .fs-title {{ font-size: 24px !important; }}
    .fs-mark {{ font-size: 26px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{OCHRE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:{OCHRE};">{_escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{OCHRE};">
<tr><td class="fs-page" align="center" style="padding:36px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="max-width:600px;background:{PAPER};border:2px solid {INK};border-radius:10px;">
<tr><td style="background:{SCARLET};border-radius:8px 8px 0 0;">
{_sprocket_row()}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td align="center" style="padding:14px 24px 10px;">
<div class="fs-mark" style="font-family:{SERIF};font-style:italic;font-weight:bold;font-size:32px;line-height:1.1;color:{MARIGOLD};">Fake Sangeet</div>
<div style="font-family:{SANS};font-size:12px;letter-spacing:2px;color:{PAPER};padding-top:6px;">band baaja no baarat</div>
</td></tr></table>
{_sprocket_row()}
</td></tr>
<tr><td class="fs-body" style="padding:34px 40px 18px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK};">
<h1 class="fs-title" style="margin:0 0 18px;font-family:{SERIF};font-size:28px;line-height:1.2;font-weight:bold;color:{SCARLET};">{_escape(heading)}</h1>
{body}
{panel}
{after}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 14px;"><tr>
<td style="background:{SCARLET};border:2px solid {INK};border-radius:8px;">
<a href="{url}" target="_blank" style="display:inline-block;padding:13px 26px;font-family:{SANS};font-size:16px;font-weight:bold;color:{PAPER};text-decoration:none;">{_escape(button_label)}</a>
</td></tr></table>
<p style="margin:0 0 18px;font-size:12px;line-height:1.5;color:{MUTED};">Button not working? Paste this into your browser:<br>
<a href="{url}" style="color:{SCARLET};word-break:break-all;">{url}</a></p>
</td></tr>
<tr><td class="fs-foot" style="padding:22px 40px 28px;border-top:2px dashed #E3C38E;font-family:{SANS};">
{_footer_html()}
</td></tr>
</table>
</td></tr></table>
</body>
</html>
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

Sign in and upload a photo of your student ID — that is the only thing left to
do. Your pass appears once it has been checked.

{DEADLINE_LINE}

{url}

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

{_footer_text()}"""

    subject = (
        "Your Fake Sangeet ticket — one step left"
        if not claim_code
        else "You have a seat at Fake Sangeet — one step left"
    )
    html = _layout(
        subject=subject,
        preheader=lead,
        heading="You have a seat at Fake Sangeet" if claim_code else "Your ticket is confirmed",
        paragraphs=[
            greeting,
            lead,
            "Sign in and upload a photo of your student ID — that is the only thing left "
            "to do. Your pass appears once it has been checked.",
        ],
        panel_label="Deadline",
        panel_text=DEADLINE_LINE,
        after_panel=(
            "Sign in with your SNU account. Anyone can open the link, but only you can "
            "see your pass.",
        ),
        button_label="Sign in and upload your ID",
        button_url=url,
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
        heading, button = "Your pass is ready", "View your pass"
    elif outcome == "RETRY_REQUIRED":
        subject = "Fake Sangeet — we need a clearer photo of your ID"
        lines = [
            "We couldn't use the photo of your student ID you sent.",
            said,
            "Sign in and send a new one — the whole card in frame, flat and in good light:",
        ]
        url = _site_url("/verify")
        heading, button = "We need a clearer photo", "Send a new photo"
    else:
        subject = "Fake Sangeet — your ID wasn't accepted"
        lines = [
            "Your student ID wasn't accepted, so there is no pass on your account.",
            said,
            "If you think that's wrong, talk to the desk.",
        ]
        url = _site_url("/")
        heading, button = "Your ID wasn't accepted", "Open Fake Sangeet"

    body = "\n\n".join(line for line in lines if line)
    text = f"""{greeting}

{body}

{url}

{_footer_text()}"""
    html = _layout(
        subject=subject,
        preheader=lines[0],
        heading=heading,
        # The note gets its own panel in HTML rather than a "They said" line,
        # straight after what went wrong; the last sentence -- the one that
        # leads into the button -- comes after it.
        paragraphs=[greeting, lines[0]],
        after_panel=tuple(line for line in lines[1:] if line and line != said),
        button_label=button,
        button_url=url,
        panel_label="Note from the team",
        panel_text=note,
    )
    return RenderedEmail(subject=subject, text_body=text, html_body=html)


#: Registry, not an if/else chain. A new template is a function plus one entry,
#: and `render` can tell "unknown" from "not implemented yet".
RENDERERS = {TEMPLATE_TICKET_READY: _ticket_ready, TEMPLATE_ID_REVIEWED: _id_reviewed}


def render(template: str, payload: dict[str, Any]) -> RenderedEmail:
    renderer = RENDERERS.get(template)
    if renderer is None:
        raise UnknownTemplate(f"no renderer for template {template!r}")
    return renderer(payload or {})
