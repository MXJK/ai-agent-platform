"""Persist reviewable Agent change sets and apply outcomes.

Revision ID: 20260809_0019
Revises: 20260808_0018
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260809_0019"
down_revision = "20260808_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_change_sets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_root", sa.Text(), nullable=False),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("apply_mode", sa.String(length=32), nullable=False),
        sa.Column("base_git_head", sa.String(length=64), nullable=True),
        sa.Column(
            "baseline_file_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "changed_files",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("patch", sa.Text(), nullable=False),
        sa.Column("patch_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column(
            "validation_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.Column("applied_by", sa.String(length=255), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("worktree_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_agent_change_sets_run_id"),
    )
    op.create_index(
        "idx_agent_change_sets_workspace_status",
        "agent_change_sets",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_agent_change_sets_workspace_status",
        table_name="agent_change_sets",
    )
    op.drop_table("agent_change_sets")
