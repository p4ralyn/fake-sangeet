from email.message import EmailMessage as MimeMessage

import aiosmtplib
import structlog

from app.core.settings import get_settings
from app.services.mailer.provider import EmailMessage

log = structlog.get_logger()


class SmtpMailer:
    async def send(self, message: EmailMessage) -> None:
        settings = get_settings()
        mime = MimeMessage()
        mime["From"] = settings.smtp_from
        mime["To"] = str(message.to)
        mime["Subject"] = message.subject
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        smtp = aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=settings.smtp_use_tls or None,
        )
        async with smtp:
            if settings.smtp_username:
                # AUTH PLAIN by name, never the library's generic `login()`.
                # Gmail now advertises several token methods (XOAUTH2,
                # PLAIN-CLIENTTOKEN, OAUTHBEARER, ...), and the negotiation in
                # `login()` -- aiosmtplib's and Python's smtplib's alike -- gets
                # the connection dropped with "Unexpected EOF" before a password
                # is even checked. Asking for PLAIN gets a real answer: accepted,
                # or "535 Username and Password not accepted". Found 5 Oct 2026,
                # when the first real send through snuphoria@snu.edu.in failed.
                await smtp.auth_plain(settings.smtp_username, settings.smtp_password)
            await smtp.send_message(mime)
        # `to` holds an email address; redact_pii hashes it via its inline-email
        # regex match on the value even though the key "to" itself isn't in
        # PII_KEYS — verified against app/core/logging.py rather than assumed.
        log.info("email_sent", to=str(message.to), template=message.subject)
