"""Add durable Query message identities for atomic start and completion.

Revision ID: 20260813_0021
Revises: 20260810_0020
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0021"
down_revision = "20260810_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("source_run_id", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_source_run_id",
        "messages",
        "agent_runs",
        ["source_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_messages_query_run_role",
        "messages",
        ["source_run_id", "role"],
        unique=True,
        postgresql_where=sa.text("source_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_messages_query_run_role", table_name="messages")
    op.drop_constraint(
        "fk_messages_source_run_id",
        "messages",
        type_="foreignkey",
    )
    op.drop_column("messages", "source_run_id")
