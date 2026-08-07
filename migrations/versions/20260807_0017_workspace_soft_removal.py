"""Allow registered workspaces to be removed without deleting history.

Revision ID: 20260807_0017
Revises: 20260807_0016
Create Date: 2026-08-07 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0017"
down_revision = "20260807_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_workspaces_active_id",
        "workspaces",
        ["id"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_workspaces_active_id", table_name="workspaces")
    op.drop_column("workspaces", "removed_at")
