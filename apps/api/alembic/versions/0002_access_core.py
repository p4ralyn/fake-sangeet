"""M2 access core: orders, tickets, passes, scans + the scans append-only trigger.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.access import SCAN_RESULT_CHECK, PassState, ScanAction
from app.models.ticketing import TICKETS_ONE_PER_IDENTITY_WHERE, PartnerMode, TicketStatus

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum labels come from the Python enums, so the type and the model cannot drift.
TICKET_STATUS = postgresql.ENUM(
    *(s.value for s in TicketStatus), name="ticket_status", create_type=False
)
PARTNER_MODE = postgresql.ENUM(
    *(m.value for m in PartnerMode), name="partner_mode", create_type=False
)
PASS_STATE = postgresql.ENUM(*(s.value for s in PassState), name="pass_state", create_type=False)
SCAN_ACTION = postgresql.ENUM(*(a.value for a in ScanAction), name="scan_action", create_type=False)

# LLD-v1 §12: "APPEND ONLY. never UPDATE, never DELETE." Enforced here rather
# than by convention -- M1 carried this as deferred until `scans` existed.
# Statement-level so it fires even when the statement would match zero rows:
# a row-level trigger on an UPDATE that hits nothing raises nothing, which
# would let "it didn't error" be mistaken for "the table is immutable".
APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION scans_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'scans is append-only: % is not permitted', TG_OP
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    TICKET_STATUS.create(bind, checkfirst=True)
    PARTNER_MODE.create(bind, checkfirst=True)
    PASS_STATE.create(bind, checkfirst=True)
    SCAN_ACTION.create(bind, checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("purchaser_name", sa.Text()),
        sa.Column("purchaser_email", postgresql.CITEXT(), nullable=False),
        sa.Column("purchaser_phone", sa.Text()),
        sa.Column("net_id_hint", postgresql.CITEXT()),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ticket_type", sa.Text(), nullable=False, server_default="SINGLE"),
        sa.Column("amount_paid", sa.Numeric(10, 2)),
        sa.Column("currency", sa.Text(), nullable=False, server_default="INR"),
        sa.Column("payment_status", sa.Text(), nullable=False, server_default="PAID"),
        sa.Column("payment_ref", sa.Text()),
        sa.Column("purchased_at", sa.DateTime(timezone=True)),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", "external_id", name="orders_event_external_key"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 20", name="orders_quantity_range"),
    )
    op.create_index("ix_orders_event_id", "orders", ["event_id"])

    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_ref", sa.Text(), nullable=False),
        sa.Column("claim_code", sa.Text()),
        sa.Column("seq_in_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", TICKET_STATUS, nullable=False, server_default="PURCHASED"),
        sa.Column("ticket_type", sa.Text(), nullable=False, server_default="SINGLE"),
        sa.Column("partner_mode", PARTNER_MODE),
        sa.Column("partner_of", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="SET NULL")),
        sa.Column("phase_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("bound_net_id", postgresql.CITEXT()),
        sa.Column("bound_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", "ticket_ref", name="tickets_event_ref_key"),
        sa.CheckConstraint("claim_code IS NULL OR partner_mode = 'SELF_CLAIM'", name="tickets_claim_code_requires_self_claim"),
        sa.CheckConstraint("partner_of IS NULL OR partner_of <> id", name="tickets_partner_not_self"),
        sa.CheckConstraint("seq_in_order >= 1", name="tickets_seq_positive"),
    )
    op.create_index("ix_tickets_event_id", "tickets", ["event_id"])
    op.create_index("ix_tickets_event_status", "tickets", ["event_id", "status"])
    op.create_index("ix_tickets_order_id", "tickets", ["order_id"])
    # One identity ↔ one live ticket per event. Predicate text is generated from
    # TICKET_DEAD_STATUSES so it cannot drift from the application's idea of dead.
    op.create_index(
        "tickets_one_per_identity",
        "tickets",
        ["event_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(TICKETS_ONE_PER_IDENTITY_WHERE),
    )

    op.create_table(
        "passes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("serial", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("short_code", sa.Text(), nullable=False),
        sa.Column("qr_object", sa.Text()),
        sa.Column("current_state", PASS_STATE, nullable=False, server_default="NOT_ARRIVED"),
        sa.Column("breaks_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_checkin_at", sa.DateTime(timezone=True)),
        sa.Column("last_exit_at", sa.DateTime(timezone=True)),
        sa.Column("checked_out_at", sa.DateTime(timezone=True)),
        sa.Column("last_scan_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "short_code", name="passes_event_short_code_key"),
        sa.CheckConstraint("serial >= 1", name="passes_serial_positive"),
        sa.CheckConstraint("breaks_used >= 0", name="passes_breaks_used_non_negative"),
        sa.CheckConstraint("version >= 0", name="passes_version_non_negative"),
    )
    op.create_index("ix_passes_event_id", "passes", ["event_id"])
    op.create_index("ix_passes_event_state", "passes", ["event_id", "current_state"])

    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pass_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("passes.id")),
        sa.Column("gate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("gates.id")),
        sa.Column("scanned_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("device_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", SCAN_ACTION, nullable=False),
        sa.Column("from_state", PASS_STATE),
        sa.Column("to_state", PASS_STATE),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("deny_reason", sa.Text()),
        sa.Column("override_reason", sa.Text()),
        sa.Column("offline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("client_scan_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("raw_token_hash", sa.Text()),
        sa.UniqueConstraint("pass_id", "client_scan_uuid", name="scans_pass_client_uuid_key"),
        sa.CheckConstraint(SCAN_RESULT_CHECK, name="scans_result_known"),
    )
    op.create_index("ix_scans_event_id", "scans", ["event_id"])
    op.create_index("ix_scans_event_occurred", "scans", ["event_id", sa.text("occurred_at DESC")])
    op.create_index("ix_scans_pass_occurred", "scans", ["pass_id", "occurred_at"])
    op.create_index("ix_scans_event_gate_occurred", "scans", ["event_id", "gate_id", sa.text("occurred_at DESC")])

    op.execute(APPEND_ONLY_FN)
    op.execute(
        "CREATE TRIGGER scans_no_update BEFORE UPDATE ON scans "
        "FOR EACH STATEMENT EXECUTE FUNCTION scans_append_only()"
    )
    op.execute(
        "CREATE TRIGGER scans_no_delete BEFORE DELETE ON scans "
        "FOR EACH STATEMENT EXECUTE FUNCTION scans_append_only()"
    )


def downgrade() -> None:
    # The triggers block DELETE, not DROP TABLE -- but drop them first anyway so
    # a partial downgrade never leaves a trigger pointing at a missing function.
    op.execute("DROP TRIGGER IF EXISTS scans_no_delete ON scans")
    op.execute("DROP TRIGGER IF EXISTS scans_no_update ON scans")
    op.drop_table("scans")
    op.execute("DROP FUNCTION IF EXISTS scans_append_only()")
    op.drop_table("passes")
    op.drop_table("tickets")
    op.drop_table("orders")
    bind = op.get_bind()
    SCAN_ACTION.drop(bind, checkfirst=True)
    PASS_STATE.drop(bind, checkfirst=True)
    PARTNER_MODE.drop(bind, checkfirst=True)
    TICKET_STATUS.drop(bind, checkfirst=True)
