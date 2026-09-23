"""One transfer per ticket, ever — enforced by the database.

Adds `tickets.transfer_count` and the CHECK that caps it. The cap is rendered
from `MAX_TRANSFERS_PER_TICKET` rather than typed here, so the constant and the
constraint cannot drift.

The constraint, not the service, is what makes this true. The service refuses
first so that somebody gets a sentence instead of a constraint violation — but
if that check were ever bypassed, edited around, or lost to a race, the
database still refuses the second transfer.

Safe on live data: every existing ticket defaults to 0, and the tickets already
ingested have never been transferred.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.ticketing import TRANSFER_COUNT_CHECK

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("transfer_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint("tickets_transfer_limit", "tickets", TRANSFER_COUNT_CHECK)


def downgrade() -> None:
    op.drop_constraint("tickets_transfer_limit", "tickets", type_="check")
    op.drop_column("tickets", "transfer_count")
