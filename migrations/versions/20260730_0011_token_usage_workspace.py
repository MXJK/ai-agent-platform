"""Attribute token usage to workspaces and persist thinking tokens.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0011"
down_revision = "20260730_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_usage_records",
        sa.Column("workspace_id", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_token_usage_records_workspace",
        "token_usage_records",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "token_usage_records",
        sa.Column(
            "thoughts_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_token_usage_records_thoughts_nonnegative",
        "token_usage_records",
        "thoughts_tokens >= 0",
    )
    op.create_index(
        "idx_token_usage_workspace_created",
        "token_usage_records",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_token_usage_workspace_created",
        table_name="token_usage_records",
    )
    op.drop_constraint(
        "ck_token_usage_records_thoughts_nonnegative",
        "token_usage_records",
        type_="check",
    )
    op.drop_column("token_usage_records", "thoughts_tokens")
    op.drop_constraint(
        "fk_token_usage_records_workspace",
        "token_usage_records",
        type_="foreignkey",
    )
    op.drop_column("token_usage_records", "workspace_id")
