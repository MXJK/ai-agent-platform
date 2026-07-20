"""Prevent concurrent active index jobs for one repository.

Revision ID: 20260720_0005
Revises: 20260715_0004
Create Date: 2026-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_0005"
down_revision = "20260715_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_repository_index_jobs_active_repository",
        "repository_index_jobs",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_repository_index_jobs_active_repository",
        table_name="repository_index_jobs",
    )
