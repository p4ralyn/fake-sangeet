"""M3 ingestion: ticket_sources, ingestion_errors, email_outbox, inventory_ledger.

Expand-only. Nothing is dropped and nothing existing changes shape; the two
additions to live tables are a nullable `orders.source_id` and a partial unique
index on `tickets.bound_net_id`, both safe against the rows M2 can already have
written (there are none in any environment, but the migration does not rely on
that).

`inventory_ledger` lands here rather than at M5 because M3 is where the first
seat actually moves. CLAUDE.md: every seat movement writes a ledger row.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.event import LEDGER_REASON_CHECK
from app.models.ingestion import EMAIL_STATUS_CHECK
from app.models.ticketing import TICKETS_ONE_PER_NET_ID_WHERE

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_watermark", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_ticket_sources_event_id", "ticket_sources", ["event_id"])
    op.create_index(
        "ticket_sources_event_key", "ticket_sources", ["event_id", "source_key"], unique=True
    )

    # LLD-v1 §12 has this column on `orders`; M2 created the table before
    # `ticket_sources` existed, so it arrives now. Nullable — an M5 on-platform
    # order has no external source.
    op.add_column(
        "orders", sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "orders_source_id_fkey", "orders", "ticket_sources", ["source_id"], ["id"],
        ondelete="SET NULL",
    )

    # An ingested ticket has no user_id yet, so `tickets_one_per_identity` cannot
    # see it. This index is the same invariant one step earlier: the NetID the
    # row is destined for can hold exactly one live ticket.
    op.create_index(
        "tickets_one_per_net_id",
        "tickets",
        ["event_id", "bound_net_id"],
        unique=True,
        postgresql_where=sa.text(TICKETS_ONE_PER_NET_ID_WHERE),
    )

    op.create_table(
        "inventory_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        # No FK: payment_intents is M5. Same deferral as passes.registration_id.
        sa.Column("intent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("seats_committed_after", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(LEDGER_REASON_CHECK, name="inventory_ledger_reason_known"),
        sa.CheckConstraint("delta <> 0", name="inventory_ledger_delta_non_zero"),
        sa.CheckConstraint("seats_committed_after >= 0", name="inventory_ledger_after_non_negative"),
    )
    op.create_index(
        "ix_inventory_ledger_event_created",
        "inventory_ledger",
        ["event_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "ingestion_errors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ticket_sources.id", ondelete="SET NULL")),
        sa.Column("row_ref", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ingestion_errors_event_id", "ingestion_errors", ["event_id"])
    op.create_index(
        "ix_ingestion_errors_event_unresolved", "ingestion_errors", ["event_id", "resolved_at"]
    )
    # One open problem per (source row, reason), so a re-run re-reports rather
    # than re-accumulating. The service inserts ON CONFLICT DO NOTHING.
    op.create_index(
        "ingestion_errors_open_row_key",
        "ingestion_errors",
        ["event_id", "source_id", "row_ref", "reason"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.create_table(
        "email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        # No FK: `registrations` is M4 (LLD-v1 §12 specifies the column).
        sa.Column("registration_id", postgresql.UUID(as_uuid=True)),
        sa.Column("to_email", postgresql.CITEXT(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_message_id", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        # THE anti-double-send. A second ingest run conflicts here and nobody
        # receives "your ticket is ready" twice.
        sa.UniqueConstraint("idempotency_key", name="email_outbox_idempotency_key_key"),
        sa.CheckConstraint(EMAIL_STATUS_CHECK, name="email_outbox_status_known"),
        sa.CheckConstraint("attempts >= 0", name="email_outbox_attempts_non_negative"),
    )
    op.create_index("ix_email_outbox_event_id", "email_outbox", ["event_id"])
    op.create_index(
        "ix_email_outbox_status_next_attempt", "email_outbox", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_table("email_outbox")
    op.drop_table("ingestion_errors")
    op.drop_table("inventory_ledger")
    op.drop_index("tickets_one_per_net_id", table_name="tickets")
    op.drop_constraint("orders_source_id_fkey", "orders", type_="foreignkey")
    op.drop_column("orders", "source_id")
    op.drop_table("ticket_sources")
