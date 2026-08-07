"""Add manageable knowledge-document metadata.

Revision ID: 20260807_0016
Revises: 20260807_0015
Create Date: 2026-08-07 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260807_0016"
down_revision = "20260807_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("title", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "documents",
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "documents",
        sa.Column("media_type", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE documents
        SET
            title = filename,
            chunk_count = chunk_totals.value,
            updated_at = documents.created_at,
            indexed_at = documents.created_at,
            media_type = CASE
                WHEN LOWER(filename) LIKE '%.pdf' THEN 'application/pdf'
                WHEN LOWER(filename) LIKE '%.docx' THEN
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                WHEN LOWER(filename) LIKE '%.md'
                  OR LOWER(filename) LIKE '%.markdown' THEN 'text/markdown'
                WHEN LOWER(filename) LIKE '%.json' THEN 'application/json'
                ELSE NULL
            END
        FROM (
            SELECT document_id, COUNT(*)::integer AS value
            FROM document_chunks
            GROUP BY document_id
        ) AS chunk_totals
        WHERE chunk_totals.document_id = documents.id
        """
    )
    op.execute(
        """
        UPDATE documents
        SET title = filename,
            updated_at = created_at,
            indexed_at = created_at
        WHERE title IS NULL
        """
    )
    op.alter_column("documents", "title", nullable=False)
    op.alter_column("documents", "updated_at", nullable=False)
    op.create_index(
        "idx_documents_kb_updated",
        "documents",
        ["knowledge_base_id", "updated_at"],
    )
    op.create_index(
        "idx_documents_kb_title",
        "documents",
        ["knowledge_base_id", "title"],
    )


def downgrade() -> None:
    op.drop_index("idx_documents_kb_title", table_name="documents")
    op.drop_index("idx_documents_kb_updated", table_name="documents")
    op.drop_column("documents", "indexed_at")
    op.drop_column("documents", "updated_at")
    op.drop_column("documents", "chunk_count")
    op.drop_column("documents", "byte_size")
    op.drop_column("documents", "media_type")
    op.drop_column("documents", "tags")
    op.drop_column("documents", "description")
    op.drop_column("documents", "title")
