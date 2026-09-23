"""Pass transfer: a registration can be superseded when a ticket changes hands.

Expand-only, and the one interesting part is the swap from a plain UNIQUE on
`registrations.ticket_id` to a PARTIAL unique index.

The old constraint said "one registration per ticket, ever". Transfer needs
"one LIVE registration per ticket" — the previous holder's row has to stay,
because it carries the evidence that somebody's ID was checked, and deleting
that to make room for the new holder would destroy an audit trail to save a
row. The partial index keeps the guarantee where it matters and lets history
accumulate underneath it.

Safe on live data: every existing row has `superseded_at IS NULL`, so the
partial index covers exactly the rows the old constraint did.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "registrations", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Drop first, then create under the same name: the model calls it
    # `registrations_ticket_key` either way, so autogenerate stays quiet.
    op.drop_constraint("registrations_ticket_key", "registrations", type_="unique")
    op.create_index(
        "registrations_ticket_key",
        "registrations",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    # Only reversible while no ticket has actually been transferred; a second
    # live registration for one ticket cannot exist, but a superseded one can,
    # and the plain UNIQUE would refuse it.
    op.drop_index("registrations_ticket_key", table_name="registrations")
    op.execute("DELETE FROM registrations WHERE superseded_at IS NOT NULL")
    op.create_unique_constraint("registrations_ticket_key", "registrations", ["ticket_id"])
    op.drop_column("registrations", "superseded_at")
