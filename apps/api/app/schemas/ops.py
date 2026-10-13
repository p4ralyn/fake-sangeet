"""Shapes for the ops dashboard.

Everything here is a COUNT or a reference, never an attendee's details — with
one deliberate exception, the ingestion tray, which exists precisely so an
admin can read a broken sheet row and go fix it. That row is the reason these
endpoints are ADMIN/OWNER only.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class Occupancy(BaseModel):
    """Who is where, right now. The number an OWNER wants at 18:30."""

    inside: int = 0
    on_break: int = 0
    departed: int = 0
    not_arrived: int = 0
    revoked: int = 0


class Seats(BaseModel):
    committed: int
    total: int


class Queues(BaseModel):
    """The three things that can silently build up."""

    ids_waiting: int = 0
    #: Longest an ID has sat unreviewed, in minutes. The number that decides
    #: whether the review desk is keeping up.
    ids_oldest_minutes: int | None = None
    ingestion_unresolved: int = 0
    mail_queued: int = 0
    mail_dead: int = 0


class OpsSummary(BaseModel):
    occupancy: Occupancy
    seats: Seats
    queues: Queues
    #: Scans in the last window, as a sign of life — a gate that has gone quiet
    #: at 18:20 is either finished or broken, and the difference matters.
    scans_recent: int = 0
    scans_window_minutes: int
    #: Refused scans in the same window. §11.5 treats these as worth an alert,
    #: not just a log line.
    refusals_recent: int = 0
    generated_at: datetime


class MailProblem(BaseModel):
    """An outbox row that needs a person. Addresses are shown because the
    question is always "who didn't get theirs"."""

    id: UUID
    to_email: str
    template: str
    status: str
    attempts: int
    last_error: str | None = None
    next_attempt_at: datetime | None = None


class MailHealth(BaseModel):
    queued: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = 0
    dead: int = 0
    #: Only the rows worth looking at: dead, or retrying after a failure.
    problems: list[MailProblem] = Field(default_factory=list)
