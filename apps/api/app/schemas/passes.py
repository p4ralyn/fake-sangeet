"""The participant's own pass (M6).

`qr_token`, not the contract's original `qr_url`. That field was named when a
server-rendered PNG was assumed; the QR is drawn in the browser from this
string instead, so what travels here is the pass token itself — the exact bytes
the scanner decodes and posts to `/api/v1/scans`.

Renaming it matters more than the churn costs. A bearer credential in a field
called `qr_url` is a thing people log, paste into a bug report and forward,
because the name says it is an address. `qr_token` says what it is.
"""
from enum import StrEnum

from pydantic import BaseModel

from app.models.access import PassState
from app.models.ticketing import TicketStatus


class TicketViewState(StrEnum):
    """What the pass screen shows. Collapses `tickets.status` and
    `passes.current_state` into the one answer a person wants: where am I."""

    ISSUED = "ISSUED"
    ID_PENDING = "ID_PENDING"
    ID_VERIFIED = "ID_VERIFIED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    CHECKED_IN = "CHECKED_IN"
    EXIT_BREAK = "EXIT_BREAK"
    CHECKED_OUT = "CHECKED_OUT"
    REVOKED = "REVOKED"


# The pass's own state wins wherever it has an opinion: once somebody is
# through the door, "inside" is the only fact that matters to them.
_FROM_PASS_STATE: dict[PassState, TicketViewState] = {
    PassState.NOT_ARRIVED: TicketViewState.ISSUED,
    PassState.INSIDE: TicketViewState.CHECKED_IN,
    PassState.ON_BREAK: TicketViewState.EXIT_BREAK,
    PassState.DEPARTED: TicketViewState.CHECKED_OUT,
    PassState.REVOKED: TicketViewState.REVOKED,
}

# Before a pass exists, the ticket's status is the whole story.
_FROM_TICKET_STATUS: dict[TicketStatus, TicketViewState] = {
    TicketStatus.PURCHASED: TicketViewState.ID_PENDING,
    TicketStatus.CLAIMED: TicketViewState.ID_PENDING,
    TicketStatus.PENDING_OCR: TicketViewState.ID_PENDING,
    TicketStatus.RETRY_REQUIRED: TicketViewState.ID_PENDING,
    TicketStatus.MANUAL_REVIEW: TicketViewState.MANUAL_REVIEW,
    TicketStatus.VERIFIED: TicketViewState.ID_VERIFIED,
    TicketStatus.PASS_ISSUED: TicketViewState.ISSUED,
    TicketStatus.REJECTED: TicketViewState.REVOKED,
    TicketStatus.EXPIRED_UNCLAIMED: TicketViewState.REVOKED,
    TicketStatus.CANCELLED: TicketViewState.REVOKED,
    TicketStatus.REVOKED: TicketViewState.REVOKED,
}

# Fail at import if a status is ever added without deciding what a participant
# should see. A KeyError at 6pm on the night is not the time to find out.
assert set(_FROM_TICKET_STATUS) == set(TicketStatus)
assert set(_FROM_PASS_STATE) == set(PassState)


def view_state(ticket_status: TicketStatus, pass_state: PassState | None) -> TicketViewState:
    if pass_state is not None:
        return _FROM_PASS_STATE[pass_state]
    return _FROM_TICKET_STATUS[ticket_status]


class PassTicket(BaseModel):
    short_code: str
    #: The QR payload. A bearer credential: never log it, never store it
    #: client-side, never put it in a URL.
    qr_token: str | None
    holder_name: str
    seat_index: int
    seats: int
    state: TicketViewState


class MyPassResponse(BaseModel):
    tickets: list[PassTicket]
