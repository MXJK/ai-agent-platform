"""Add durable pending Agent context compaction requests.

Revision ID: 20260831_0026
Revises: 20260825_0025
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260831_0026"
down_revision = "20260825_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "pending_compaction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "pending_compaction")
