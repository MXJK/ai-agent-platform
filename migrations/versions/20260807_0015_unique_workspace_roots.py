"""Require one workspace identity per canonical root path.

Revision ID: 20260807_0015
Revises: 20260804_0014
Create Date: 2026-08-07 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0015"
down_revision = "20260804_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT root_path
            FROM workspaces
            GROUP BY root_path
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate workspace root paths must be resolved before migration"
        )
    op.create_unique_constraint(
        "uq_workspaces_root_path",
        "workspaces",
        ["root_path"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workspaces_root_path",
        "workspaces",
        type_="unique",
    )
