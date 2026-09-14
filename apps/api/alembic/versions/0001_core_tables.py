"""M1 core tables: events, gates, inventory, users, staff, audit, funnel.

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAFF_ROLE = postgresql.ENUM("OWNER", "ADMIN", "SECURITY", name="staff_role", create_type=False)
GATE_TYPE = postgresql.ENUM("ENTRY_ONLY", "EXIT_ONLY", "BOTH", name="gate_type", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    bind = op.get_bind()
    STAFF_ROLE.create(bind, checkfirst=True)
    GATE_TYPE.create(bind, checkfirst=True)

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("venue", sa.Text()),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True)),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("ends_at > starts_at", name="ends_after_start"),
    )

    op.create_table(
        "gates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", GATE_TYPE, nullable=False, server_default="BOTH"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("event_id", "name", name="gates_event_name_key"),
    )
    op.create_index("ix_gates_event_id", "gates", ["event_id"])

    # Addendum §A1. ONE row per event. `no_oversell` is the last line of defence.
    op.create_table(
        "event_inventory",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seats_total", sa.Integer(), nullable=False),
        sa.Column("seats_committed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("seats_total > 0", name="seats_total_positive"),
        sa.CheckConstraint("seats_committed >= 0", name="seats_committed_non_negative"),
        sa.CheckConstraint("seats_committed <= seats_total", name="no_oversell"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ms_object_id", sa.Text(), unique=True),
        sa.Column("net_id", postgresql.CITEXT()),
        sa.Column("email", postgresql.CITEXT()),
        sa.Column("display_name", sa.Text()),
        sa.Column("auth_method", sa.Text(), nullable=False, server_default="MICROSOFT_SSO"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_net_id", "users", ["net_id"])

    op.create_table(
        "event_staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("net_id", postgresql.CITEXT(), nullable=False),
        sa.Column("role", STAFF_ROLE, nullable=False),
        sa.Column("gate_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", "net_id", "role", name="event_staff_event_netid_role_key"),
    )
    op.create_index("ix_event_staff_event_id", "event_staff", ["event_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("actor_net_id", postgresql.CITEXT()),
        sa.Column("actor_role", STAFF_ROLE),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text()),
        sa.Column("object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("ip_hash", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_event_created", "audit_log", ["event_id", sa.text("created_at DESC")])
    op.create_index("ix_audit_log_object", "audit_log", ["object_type", "object_id"])

    op.create_table(
        "funnel_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_funnel_events_event_step", "funnel_events", ["event_id", "step", "created_at"])


def downgrade() -> None:
    op.drop_table("funnel_events")
    op.drop_table("audit_log")
    op.drop_table("event_staff")
    op.drop_table("users")
    op.drop_table("event_inventory")
    op.drop_table("gates")
    op.drop_table("events")
    bind = op.get_bind()
    GATE_TYPE.drop(bind, checkfirst=True)
    STAFF_ROLE.drop(bind, checkfirst=True)
