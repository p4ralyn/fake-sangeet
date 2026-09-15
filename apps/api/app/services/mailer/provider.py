"""Mail seam. ONE implementation: mailpit and Google Workspace both speak SMTP,
so the difference is host, TLS and credentials — configuration, not code."""
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, EmailStr


class EmailMessage(BaseModel):
    to: EmailStr
    subject: str
    text_body: str
    html_body: str | None = None


@runtime_checkable
class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


def get_mailer() -> Mailer:
    from app.services.mailer.smtp import SmtpMailer

    return SmtpMailer()
