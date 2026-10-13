"""Baarati or gharwale: a fixed tag on every seat.

Client, 14 Oct 2026: every ticket sold so far is BAARATI; GHARWALE arrives
later with its own sheet. The tag is a Postgres enum so nothing outside its two
values can be written, and it sits on `tickets` because it belongs to the seat
-- a transfer keeps it.

Backfill and default: the column is added with `server_default 'BAARATI'`,
which stamps every existing row, and the default is then DROPPED. From here on
whoever mints a ticket must say which side it is on, so a gharwale ingest that
forgot `--guest-tag` cannot quietly make baaratis.

Safe on live data: one ALTER adds a NOT NULL column with a constant default,
which Postgres applies without rewriting the table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

guest_tag = postgresql.ENUM("BAARATI", "GHARWALE", name="guest_tag", create_type=False)


def upgrade() -> None:
    postgresql.ENUM("BAARATI", "GHARWALE", name="guest_tag").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "tickets",
        sa.Column("guest_tag", guest_tag, nullable=False, server_default="BAARATI"),
    )
    op.alter_column("tickets", "guest_tag", server_default=None)


def downgrade() -> None:
    op.drop_column("tickets", "guest_tag")
    postgresql.ENUM(name="guest_tag").drop(op.get_bind(), checkfirst=True)
