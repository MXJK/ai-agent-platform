"""Persist the immutable execution context captured for each Agent Run.

Revision ID: 20260810_0020
Revises: 20260809_0019
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260810_0020"
down_revision = "20260809_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "run_context_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "run_context_snapshot")
