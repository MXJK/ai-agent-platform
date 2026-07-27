"""Add RAG lexical search metadata and index job state.

Revision ID: 20260727_0008
Revises: 20260724_0007
Create Date: 2026-07-27 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260727_0008"
down_revision = "20260724_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("start_line", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("end_line", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "symbols",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "search_text",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.execute(
        """
        UPDATE document_chunks
        SET search_text = filename || ' ' || text
        """
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, search_text)",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "rag_index_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Text(),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column(
            "chunk_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'parsing', 'embedding', "
            "'vector_written', 'active', 'failed'"
            ")",
            name="ck_rag_index_jobs_status",
        ),
    )
    op.create_index(
        "idx_rag_index_jobs_kb_created",
        "rag_index_jobs",
        ["knowledge_base_id", "created_at"],
    )
    op.create_index(
        "idx_rag_index_jobs_status_updated",
        "rag_index_jobs",
        ["status", "updated_at"],
    )
    op.create_index(
        "uq_rag_index_jobs_active_document",
        "rag_index_jobs",
        ["knowledge_base_id", "filename"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'parsing', 'embedding', 'vector_written')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_rag_index_jobs_active_document",
        table_name="rag_index_jobs",
    )
    op.drop_index(
        "idx_rag_index_jobs_status_updated",
        table_name="rag_index_jobs",
    )
    op.drop_index(
        "idx_rag_index_jobs_kb_created",
        table_name="rag_index_jobs",
    )
    op.drop_table("rag_index_jobs")
    op.drop_index(
        "idx_document_chunks_search_vector",
        table_name="document_chunks",
    )
    op.drop_column("document_chunks", "search_vector")
    op.drop_column("document_chunks", "search_text")
    op.drop_column("document_chunks", "symbols")
    op.drop_column("document_chunks", "end_line")
    op.drop_column("document_chunks", "start_line")
