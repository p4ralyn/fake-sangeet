"""Everybody is a baarati: the guest tag shrinks to one value.

Client, 14 Oct 2026, the same day 0008 added BAARATI/GHARWALE: the gharwale do
not use this system at all -- they come and go without the door's checks -- so
GHARWALE is removed. The tag itself stays, shown at the door and on the pass.

Postgres cannot drop a value from an enum, so the type is swapped: rename the
old one, create `guest_tag` with only BAARATI, recast the column, drop the old
type. The recast fails loudly if any row were GHARWALE, which is the point:
on prod none is (0008 backfilled all 641 as BAARATI and nothing writes it).

Because there is only one value, the column default comes back as BAARATI.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-14
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE guest_tag RENAME TO guest_tag_old")
    op.execute("CREATE TYPE guest_tag AS ENUM ('BAARATI')")
    op.execute(
        "ALTER TABLE tickets ALTER COLUMN guest_tag TYPE guest_tag "
        "USING guest_tag::text::guest_tag"
    )
    op.execute("ALTER TABLE tickets ALTER COLUMN guest_tag SET DEFAULT 'BAARATI'")
    op.execute("DROP TYPE guest_tag_old")


def downgrade() -> None:
    op.execute("ALTER TABLE tickets ALTER COLUMN guest_tag DROP DEFAULT")
    op.execute("ALTER TYPE guest_tag RENAME TO guest_tag_new")
    op.execute("CREATE TYPE guest_tag AS ENUM ('BAARATI', 'GHARWALE')")
    op.execute(
        "ALTER TABLE tickets ALTER COLUMN guest_tag TYPE guest_tag "
        "USING guest_tag::text::guest_tag"
    )
    op.execute("DROP TYPE guest_tag_new")
