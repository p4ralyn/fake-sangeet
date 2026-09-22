"""M4 verification: registrations, id_images, verifications.

Expand-only. Three new tables, one new enum type, and one nullable column added
to `passes` — the `registration_id` that migration 0002 deliberately left off
because `registrations` did not exist yet.

`verify_outcome` is created here with only the outcomes a human can produce.
LLD-v1 §9.3's automatic verdicts are absent because OCR is cut (CLAUDE.md Part
3): a value in the enum that nothing can ever write is a trap for the next
person reading it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.verification import VerifyOutcome

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rendered from the enum, never hand-typed — the same rule the other
# migrations follow for their CHECKs.
_OUTCOMES = tuple(outcome.value for outcome in VerifyOutcome)


def upgrade() -> None:
    verify_outcome = postgresql.ENUM(*_OUTCOMES, name="verify_outcome")
    verify_outcome.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("net_id", postgresql.CITEXT(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("phone", sa.Text()),
        sa.Column("department", sa.Text()),
        sa.Column("year_of_study", sa.Text()),
        sa.Column("emergency_contact", sa.Text()),
        sa.Column("dietary_notes", sa.Text()),
        sa.Column("consent_version", sa.Text(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_deliverable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ticket_id", name="registrations_ticket_key"),
        sa.CheckConstraint("attempts >= 0", name="registrations_attempts_non_negative"),
    )
    op.create_index("ix_registrations_event_id", "registrations", ["event_id"])
    op.create_index("ix_registrations_event_net_id", "registrations", ["event_id", "net_id"])

    op.create_table(
        "id_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("bytes > 0", name="id_images_bytes_positive"),
    )
    op.create_index("ix_id_images_event_id", "id_images", ["event_id"])
    op.create_index("ix_id_images_registration", "id_images", ["registration_id"])
    op.create_index("ix_id_images_event_sha256", "id_images", ["event_id", "sha256"])

    op.create_table(
        "verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("id_image_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("id_images.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("outcome", postgresql.ENUM(*_OUTCOMES, name="verify_outcome", create_type=False), nullable=False),
        sa.Column("fraud_flags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("failure_reasons", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("registration_id", "attempt_no", name="verifications_registration_attempt_key"),
        sa.CheckConstraint("attempt_no >= 1", name="verifications_attempt_positive"),
        sa.CheckConstraint("(reviewed_at IS NULL) = (reviewed_by IS NULL)", name="verifications_review_pair"),
    )
    op.create_index("ix_verifications_event_id", "verifications", ["event_id"])
    op.create_index("ix_verifications_event_outcome", "verifications", ["event_id", "outcome"])
    op.create_index("ix_verifications_registration", "verifications", ["registration_id"])

    # The column 0002 left for this milestone. Nullable, so every pass minted
    # before today stays valid.
    op.add_column(
        "passes", sa.Column("registration_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "passes_registration_id_fkey", "passes", "registrations",
        ["registration_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("passes_registration_id_fkey", "passes", type_="foreignkey")
    op.drop_column("passes", "registration_id")
    op.drop_table("verifications")
    op.drop_table("id_images")
    op.drop_table("registrations")
    postgresql.ENUM(name="verify_outcome").drop(op.get_bind(), checkfirst=True)
