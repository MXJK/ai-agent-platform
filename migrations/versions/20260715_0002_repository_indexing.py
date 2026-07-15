"""Add repository indexing metadata tables.

Revision ID: 20260715_0002
Revises: 20260715_0001
Create Date: 2026-07-15 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260715_0002"
down_revision = "20260715_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("root_path", sa.Text(), nullable=False),
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
        sa.Column("last_indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "repository_index_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Text(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column(
            "include_patterns",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "exclude_patterns",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("max_file_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "scanned_files",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "indexed_files",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "skipped_files",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_files",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
    )
    op.create_index(
        "idx_repository_index_jobs_repo_created",
        "repository_index_jobs",
        ["repository_id", "created_at"],
    )
    op.create_index(
        "idx_repository_index_jobs_status_updated",
        "repository_index_jobs",
        ["status", "updated_at"],
    )

    op.create_table(
        "repository_files",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Text(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "repository_id",
            "path",
            name="uq_repository_files_repository_path",
        ),
    )
    op.create_index(
        "idx_repository_files_repo_hash",
        "repository_files",
        ["repository_id", "content_hash"],
    )
    op.create_index(
        "idx_repository_files_document",
        "repository_files",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_repository_files_document", table_name="repository_files")
    op.drop_index("idx_repository_files_repo_hash", table_name="repository_files")
    op.drop_table("repository_files")
    op.drop_index(
        "idx_repository_index_jobs_status_updated",
        table_name="repository_index_jobs",
    )
    op.drop_index(
        "idx_repository_index_jobs_repo_created",
        table_name="repository_index_jobs",
    )
    op.drop_table("repository_index_jobs")
    op.drop_table("repositories")
