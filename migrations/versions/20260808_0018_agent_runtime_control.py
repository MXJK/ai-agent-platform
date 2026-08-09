"""Add durable Agent events, controls, and tool execution identities.

Revision ID: 20260808_0018
Revises: 20260807_0017
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260808_0018"
down_revision = "20260807_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("control_action", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "steering_messages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("node", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_agent_run_event_key"),
    )
    op.create_index(
        "idx_agent_run_events_run_cursor",
        "agent_run_events",
        ["run_id", "id"],
        unique=False,
    )
    op.create_table(
        "agent_tool_executions",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "call_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_tool_executions")
    op.drop_index("idx_agent_run_events_run_cursor", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_column("agent_runs", "steering_messages")
    op.drop_column("agent_runs", "control_action")
