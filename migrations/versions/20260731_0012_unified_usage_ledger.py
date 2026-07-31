"""Expand token usage into a unified model-call ledger.

Revision ID: 20260731_0012
Revises: 20260730_0011
Create Date: 2026-07-31 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "token_usage_records",
        "session_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.add_column(
        "token_usage_records",
        sa.Column(
            "operation",
            sa.Text(),
            nullable=False,
            server_default="chat",
        ),
    )
    op.add_column(
        "token_usage_records",
        sa.Column("resource_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "token_usage_records",
        sa.Column("requested_provider", sa.Text(), nullable=True),
    )
    op.add_column(
        "token_usage_records",
        sa.Column("requested_model", sa.Text(), nullable=True),
    )
    op.add_column(
        "token_usage_records",
        sa.Column(
            "input_count_method",
            sa.Text(),
            nullable=False,
            server_default="provider_usage",
        ),
    )
    op.add_column(
        "token_usage_records",
        sa.Column(
            "budget_decision",
            sa.Text(),
            nullable=False,
            server_default="allowed",
        ),
    )
    op.create_check_constraint(
        "ck_token_usage_records_operation_nonempty",
        "token_usage_records",
        "length(operation) > 0",
    )
    op.create_check_constraint(
        "ck_token_usage_records_budget_decision",
        "token_usage_records",
        "budget_decision IN ('allowed', 'downgraded')",
    )
    op.create_index(
        "idx_token_usage_operation_created",
        "token_usage_records",
        ["operation", "created_at"],
    )
    op.create_index(
        "idx_token_usage_resource_created",
        "token_usage_records",
        ["resource_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_token_usage_resource_created",
        table_name="token_usage_records",
    )
    op.drop_index(
        "idx_token_usage_operation_created",
        table_name="token_usage_records",
    )
    op.drop_constraint(
        "ck_token_usage_records_budget_decision",
        "token_usage_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_token_usage_records_operation_nonempty",
        "token_usage_records",
        type_="check",
    )
    op.drop_column("token_usage_records", "budget_decision")
    op.drop_column("token_usage_records", "input_count_method")
    op.drop_column("token_usage_records", "requested_model")
    op.drop_column("token_usage_records", "requested_provider")
    op.drop_column("token_usage_records", "resource_id")
    op.drop_column("token_usage_records", "operation")
    op.execute("DELETE FROM token_usage_records WHERE session_id IS NULL")
    op.alter_column(
        "token_usage_records",
        "session_id",
        existing_type=sa.Text(),
        nullable=False,
    )
