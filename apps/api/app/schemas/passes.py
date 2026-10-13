"""The participant's own pass (M6).

`qr_token`, not the contract's original `qr_url`. That field was named when a
server-rendered PNG was assumed; the QR is drawn in the browser from this
string instead, so what travels here is the pass token itself — the exact bytes
the scanner decodes and posts to `/api/v1/scans`.

Renaming it matters more than the churn costs. A bearer credential in a field
called `qr_url` is a thing people log, paste into a bug report and forward,
because the name says it is an address. `qr_token` says what it is.
"""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.access import PassState
from app.models.ticketing import MAX_TRANSFERS_PER_TICKET, GuestTag, TicketStatus


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


# Ticket statuses that mean "still working through the ID check". Derived from
# the map above rather than listed again, so adding a status decides this too.
_IN_ID_FUNNEL: frozenset[TicketStatus] = frozenset(
    status
    for status, view in _FROM_TICKET_STATUS.items()
    if view
    in {
        TicketViewState.ID_PENDING,
        TicketViewState.MANUAL_REVIEW,
        TicketViewState.ID_VERIFIED,
    }
)


def view_state(ticket_status: TicketStatus, pass_state: PassState | None) -> TicketViewState:
    """What this person should be told they are.

    The pass wins wherever it has an opinion — once somebody is through the
    door, "inside" is the only fact that matters to them.

    The exception is a REVOKED pass on a ticket that is back in the ID funnel.
    That is the residue of a transfer: the pass was killed on purpose when the
    ticket changed hands, and a new one is issued once the new holder is
    verified. Letting the dead pass speak there would tell the new holder their
    ticket was revoked, which is both alarming and wrong. A pass revoked by an
    admin on a ticket that still says PASS_ISSUED is a different thing, and
    still reads as REVOKED.
    """
    if pass_state is PassState.REVOKED and ticket_status in _IN_ID_FUNNEL:
        return _FROM_TICKET_STATUS[ticket_status]
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
    #: Baarati or gharwale -- which side of the wedding this seat is on. Fixed
    #: at minting; nothing on any screen changes it.
    guest_tag: GuestTag
    #: A PROXY couple's partner seat that has not been sent to anybody. It is
    #: never served a QR — nobody's ID stands behind it — and the screen draws
    #: a lock and the way to send it instead.
    locked: bool = False


class MyPassResponse(BaseModel):
    tickets: list[PassTicket]
    #: Transferring this person's OWN ticket; null when they hold none. For a
    #: PROXY couple with the partner seat unsent, both seats move together.
    transfer: "TransferWindow | None" = None
    #: Sending the locked partner seat to the partner. Present only while
    #: there is such a seat on this account.
    partner_transfer: "TransferWindow | None" = None


class TransferRequest(BaseModel):
    """The whole form: one NetID. Nothing here identifies the ticket — which
    ticket moves is derived from who is asking, so there is nothing to tamper
    with."""

    to_net_id: str = Field(min_length=1, max_length=64)


class TransferWindow(BaseModel):
    """Whether transfer is open, when it stops, and whether this ticket has any
    move left in it. The screen shows all three; the server enforces them.

    `open` is the time window ONLY. Whether this particular ticket may still
    move is `transfers_used` against `max_transfers` — kept separate so the
    screen can say which of the two stopped it, since "come back later" and
    "this can never move again" are very different sentences.
    """

    open: bool
    closes_at: datetime
    transfers_used: int = 0
    max_transfers: int = MAX_TRANSFERS_PER_TICKET


class TransferResponse(BaseModel):
    ticket_ref: str
    to_net_id: str
    verification_required: bool
