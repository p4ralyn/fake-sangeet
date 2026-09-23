"""Find the tickets belonging to named people.

"Who is this?" is genuinely hard here, because a ticket reaches a person by
three different routes and only one of them is the obvious one:

* `bound_net_id` — the NetID off the sheet, set before they ever sign in, and
  stored as the NetID **alone** (`snuphoria`) even when the sheet named them by
  address.
* `users.email` / `users.net_id` — set once they sign in through Google.
* `partner_of` — a PROXY couple's second ticket, which carries NEITHER, because
  M3 leaves it unbound (there is nobody to claim it, and
  `tickets_one_per_net_id` would refuse a second ticket bound to the buyer).

Miss the third and a couple looks like a single. Miss the first and somebody
who has not signed in yet looks like they hold nothing — which is how the first
version of the reset script silently reported "no tickets matched".

This lives in `services/` rather than in a script because two tools need it and
a copy in each is a copy that drifts.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket
from app.models.user import User


def identity_keys(identities: list[str]) -> list[str]:
    """Every spelling of the people named, lowercased.

    An address yields both itself and its local part, because the two columns
    that can match store it differently.
    """
    keys: set[str] = set()
    for identity in identities:
        value = identity.strip().lower()
        if not value:
            continue
        keys.add(value)
        local, _, _domain = value.partition("@")
        if local != value:
            keys.add(local)
    return sorted(keys)


async def tickets_for_identities(
    db: AsyncSession, *, event_id: UUID, keys: list[str]
) -> list[Ticket]:
    """Every ticket reachable from those keys, partner tickets included."""
    if not keys:
        return []

    found: dict[UUID, Ticket] = {}

    for ticket in await db.scalars(
        select(Ticket).where(Ticket.event_id == event_id, Ticket.bound_net_id.in_(keys))
    ):
        found[ticket.id] = ticket

    user_ids = list(
        await db.scalars(select(User.id).where(User.email.in_(keys) | User.net_id.in_(keys)))
    )
    if user_ids:
        for ticket in await db.scalars(
            select(Ticket).where(Ticket.event_id == event_id, Ticket.user_id.in_(user_ids))
        ):
            found.setdefault(ticket.id, ticket)

    if found:
        for ticket in await db.scalars(
            select(Ticket).where(
                Ticket.event_id == event_id, Ticket.partner_of.in_(list(found))
            )
        ):
            found.setdefault(ticket.id, ticket)

    return sorted(found.values(), key=lambda ticket: ticket.ticket_ref)
