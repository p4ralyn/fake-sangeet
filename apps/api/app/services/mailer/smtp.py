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

        await aiosmtplib.send(
            mime,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=settings.smtp_use_tls or None,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
        )
        # `to` holds an email address; redact_pii hashes it via its inline-email
        # regex match on the value even though the key "to" itself isn't in
        # PII_KEYS — verified against app/core/logging.py rather than assumed.
        log.info("email_sent", to=str(message.to), template=message.subject)
