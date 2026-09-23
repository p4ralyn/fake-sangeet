"""What a person sees on their own pass screen (M6).

A COUPLE order mints two tickets, two passes and two QR codes, and this returns
both when one person is holding both — the PROXY case, where nobody could claim
the second seat. A SELF_CLAIM partner signs in as themselves and gets their own
single-ticket response from their own account.

The token is minted **per request**, never stored. `passes.qr_object` stays
NULL and the column earns its keep only if a rendered PNG is ever needed;
storing a bearer credential in a row, so it can be served again later, adds a
place for it to leak and buys nothing — minting is a few microseconds of HMAC.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    TICKET_LIVE_STATUSES,
    Order,
    PartnerMode,
    Pass,
    Registration,
    Ticket,
    User,
)
from app.schemas.passes import MyPassResponse, PassTicket, view_state
from app.services.passes.token import mint_pass_token


async def my_pass_view(db: AsyncSession, *, event_id: UUID, user: User) -> MyPassResponse:
    """Every live ticket this identity holds, newest order last.

    Scoped to the signed-in identity two ways — `user_id` once they have signed
    in, `bound_net_id` for a sheet row they have not yet touched — because a
    pass screen that could be reached for somebody else's ticket is the whole
    ballgame.
    """
    mine = (Ticket.user_id == user.id) | (Ticket.bound_net_id == user.net_id)

    # A PROXY couple's second ticket is deliberately unbound — M3 leaves
    # `bound_net_id` NULL because there is nobody to claim it, and because
    # `tickets_one_per_net_id` would refuse a second ticket bound to the buyer.
    # Without this clause the buyer reaches the door holding one QR for two
    # people. A SELF_CLAIM sibling is excluded by the same condition: it IS
    # bound, to the partner, who signs in and gets it on their own account.
    my_orders = (
        select(Ticket.order_id).where(Ticket.event_id == event_id, mine).scalar_subquery()
    )
    proxied_to_me = (
        Ticket.order_id.in_(my_orders)
        & (Ticket.partner_mode == PartnerMode.PROXY)
        & Ticket.user_id.is_(None)
        & Ticket.bound_net_id.is_(None)
    )

    rows = (
        await db.execute(
            select(Ticket, Pass, Order, Registration)
            .join(Order, Order.id == Ticket.order_id)
            .outerjoin(Pass, Pass.ticket_id == Ticket.id)
            .outerjoin(Registration, Registration.ticket_id == Ticket.id)
            .where(
                Ticket.event_id == event_id,
                Ticket.status.in_(TICKET_LIVE_STATUSES),
                mine | proxied_to_me,
            )
            .order_by(Ticket.order_id, Ticket.seq_in_order)
        )
    ).all()

    tickets: list[PassTicket] = []
    for ticket, issued, order, registration in rows:
        # A ticket with no pass yet still appears — "ID check pending" is a
        # state the person needs to see, and hiding the row would read as a
        # lost ticket.
        token = (
            mint_pass_token(
                event_id=event_id,
                pass_id=issued.id,
                serial=issued.serial,
                expires_at=issued.expires_at,
            )
            if issued is not None
            else None
        )
        holder = (
            (registration.full_name if registration else None)
            or (user.display_name if ticket.user_id == user.id else None)
            or order.purchaser_name
            or (ticket.bound_net_id or user.net_id)
        )
        tickets.append(
            PassTicket(
                short_code=issued.short_code if issued else "",
                qr_token=token,
                holder_name=holder,
                seat_index=ticket.seq_in_order,
                seats=order.quantity,
                state=view_state(ticket.status, issued.current_state if issued else None),
            )
        )
    return MyPassResponse(tickets=tickets)
