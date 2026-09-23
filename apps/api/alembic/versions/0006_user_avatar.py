"""`users.avatar_url` — Google's `picture` claim.

One nullable column. Everything already signed in stays valid and picks up an
avatar on their next sign-in.

Only `picture` is stored. Google's `profile` scope also returns `given_name`,
`family_name` and `locale`; none of them are read anywhere, and a column
holding somebody's personal data that nothing consumes is a liability rather
than a feature.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")
