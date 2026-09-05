"""Add durable Cogent runtime state and snapshots.

Revision ID: 20260903_0028
Revises: 20260901_0027
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260903_0028"
down_revision = "20260901_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "runtime_engine",
            sa.Text(),
            nullable=False,
            server_default="langgraph-v1",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "runtime_state_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "runtime_state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "agent_runtime_snapshots",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("boundary", sa.Text(), nullable=False),
        sa.Column("runtime_engine", sa.Text(), nullable=False),
        sa.Column("runtime_state_version", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("run_id", "snapshot_id"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.create_index(
        "idx_agent_runtime_snapshots_run_sequence",
        "agent_runtime_snapshots",
        ["run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_agent_runtime_snapshots_run_sequence",
        table_name="agent_runtime_snapshots",
    )
    op.drop_table("agent_runtime_snapshots")
    op.drop_column("agent_runs", "runtime_state_json")
    op.drop_column("agent_runs", "runtime_state_version")
    op.drop_column("agent_runs", "runtime_engine")
