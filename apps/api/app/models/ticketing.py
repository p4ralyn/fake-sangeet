"""Orders and tickets (LLD-v1 §12), amended by docs/design/cohort-and-verification.md.

`partner_mode` is per TICKET, not per event: a COUPLE sheet row that carries
partner details splits into two SELF_CLAIM tickets; one that does not stays
PROXY on the buyer's device. `events.config.couple_partner_mode` is therefore
`"PER_TICKET"` and imposes nothing.
"""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EventScoped, Timestamps, UUIDPk


class TicketStatus(StrEnum):
    PURCHASED = "PURCHASED"
    CLAIMED = "CLAIMED"
    PENDING_OCR = "PENDING_OCR"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    RETRY_REQUIRED = "RETRY_REQUIRED"
    VERIFIED = "VERIFIED"
    PASS_ISSUED = "PASS_ISSUED"  # noqa: S105 -- ticket status label, not a secret
    REJECTED = "REJECTED"
    EXPIRED_UNCLAIMED = "EXPIRED_UNCLAIMED"
    CANCELLED = "CANCELLED"
    REVOKED = "REVOKED"


class PartnerMode(StrEnum):
    """Which of the two couple-onboarding paths this ticket took."""

    SELF_CLAIM = "SELF_CLAIM"
    PROXY = "PROXY"


# The ONE source of truth for "this ticket no longer occupies an identity".
# `tickets_one_per_identity` (below) and role resolution both derive from it,
# so the index predicate and the PARTICIPANT gate can never drift apart.
TICKET_DEAD_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.CANCELLED, TicketStatus.REVOKED, TicketStatus.REJECTED}
)
TICKET_LIVE_STATUSES: frozenset[TicketStatus] = frozenset(TicketStatus) - TICKET_DEAD_STATUSES

# Rendered once, from the constant above -- never hand-typed into DDL.
_DEAD_STATUS_SQL = ", ".join(f"'{status.value}'" for status in sorted(TICKET_DEAD_STATUSES))
TICKETS_ONE_PER_IDENTITY_WHERE = (
    "user_id IS NOT NULL AND status NOT IN (" + _DEAD_STATUS_SQL + ")"
)

# The same invariant one step earlier in the funnel. An ingested ticket has no
# `user_id` yet -- the attendee has not signed in -- so `tickets_one_per_identity`
# cannot see it, and a NetID repeated across two sheet rows would mint two live
# tickets unchallenged. `bound_net_id` carries the identity the row is destined
# for, and this index refuses the second one. Same predicate source, so the two
# can never disagree about what "dead" means.
TICKETS_ONE_PER_NET_ID_WHERE = (
    "bound_net_id IS NOT NULL AND status NOT IN (" + _DEAD_STATUS_SQL + ")"
)

#: A ticket may change hands this many times, ever. One, deliberately: each
#: transfer sends a new person back through ID review, and a ticket that can
#: keep moving is a ticket nobody can be held to. Rendered into a CHECK below
#: rather than only enforced in Python — CLAUDE.md: a rule that matters gets a
#: constraint, not a comment asking nicely. Raising it needs a migration, which
#: is the right amount of friction for a policy like this.
MAX_TRANSFERS_PER_TICKET = 1

TRANSFER_COUNT_CHECK = (
    f"transfer_count >= 0 AND transfer_count <= {MAX_TRANSFERS_PER_TICKET}"
)

ticket_status_enum = ENUM(TicketStatus, name="ticket_status", create_type=False)
partner_mode_enum = ENUM(PartnerMode, name="partner_mode", create_type=False)


class Order(UUIDPk, EventScoped, Base):
    """One purchase. Seats, not orders, are the unit of capacity — a COUPLE
    order is one row here and two rows in `tickets`."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("event_id", "external_id", name="orders_event_external_key"),
        CheckConstraint("quantity BETWEEN 1 AND 20", name="orders_quantity_range"),
    )

    # Which adapter produced this order (LLD-v1 §12). Nullable: an order minted
    # on-platform in M5 has no external source at all.
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_sources.id", ondelete="SET NULL")
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    purchaser_name: Mapped[str | None] = mapped_column(Text)
    purchaser_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    purchaser_phone: Mapped[str | None] = mapped_column(Text)
    net_id_hint: Mapped[str | None] = mapped_column(CITEXT)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    ticket_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="SINGLE")
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    payment_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PAID")
    payment_ref: Mapped[str | None] = mapped_column(Text)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class Ticket(UUIDPk, EventScoped, Timestamps, Base):
    """One seat, one attendee, one pass. A COUPLE order mints two of these."""

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("event_id", "ticket_ref", name="tickets_event_ref_key"),
        # A claim code only means something when somebody else can claim it.
        CheckConstraint(
            "claim_code IS NULL OR partner_mode = 'SELF_CLAIM'",
            name="tickets_claim_code_requires_self_claim",
        ),
        CheckConstraint("partner_of IS NULL OR partner_of <> id", name="tickets_partner_not_self"),
        CheckConstraint("seq_in_order >= 1", name="tickets_seq_positive"),
        CheckConstraint(TRANSFER_COUNT_CHECK, name="tickets_transfer_limit"),
        # One identity ↔ one live ticket per event (CLAUDE.md invariant).
        Index(
            "tickets_one_per_identity",
            "event_id",
            "user_id",
            unique=True,
            postgresql_where=TICKETS_ONE_PER_IDENTITY_WHERE,
        ),
        # One NetID ↔ one live ticket per event, enforced from the moment the
        # sheet names them rather than from first sign-in.
        Index(
            "tickets_one_per_net_id",
            "event_id",
            "bound_net_id",
            unique=True,
            postgresql_where=TICKETS_ONE_PER_NET_ID_WHERE,
        ),
        Index("ix_tickets_event_status", "event_id", "status"),
        Index("ix_tickets_order_id", "order_id"),
    )

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    ticket_ref: Mapped[str] = mapped_column(Text, nullable=False)
    claim_code: Mapped[str | None] = mapped_column(Text)
    seq_in_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[TicketStatus] = mapped_column(
        ticket_status_enum, nullable=False, server_default="PURCHASED"
    )
    ticket_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="SINGLE")
    partner_mode: Mapped[PartnerMode | None] = mapped_column(partner_mode_enum)
    partner_of: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    # Phase 3 is the on-platform cohort (LLD-v2 §6). No FK: `phases` is a config
    # list, not a table, in the single-event edition.
    phase_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    #: How many times this ticket has changed hands. The CHECK above is what
    #: actually stops a second transfer; the service refuses first so the
    #: person gets a sentence rather than a constraint violation.
    transfer_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bound_net_id: Mapped[str | None] = mapped_column(CITEXT)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
