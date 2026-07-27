"""Add managed knowledge-base catalog metadata.

Revision ID: 20260724_0007
Revises: 20260723_0006
Create Date: 2026-07-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260724_0007"
down_revision = "20260723_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        """
        INSERT INTO knowledge_bases (
            id, name, description, tags, created_at, updated_at
        )
        SELECT
            knowledge_base_id,
            knowledge_base_id,
            '',
            '[]'::jsonb,
            MIN(created_at),
            NOW()
        FROM documents
        GROUP BY knowledge_base_id
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.create_foreign_key(
        "fk_documents_knowledge_base",
        "documents",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_knowledge_base",
        "documents",
        type_="foreignkey",
    )
    op.drop_table("knowledge_bases")
