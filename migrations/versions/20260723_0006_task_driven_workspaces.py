"""Replace repository indexing with registered workspaces.

Revision ID: 20260723_0006
Revises: 20260720_0005
Create Date: 2026-07-23 14:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260723_0006"
down_revision = "20260720_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("repository_files")
    op.drop_table("repository_index_jobs")
    op.rename_table("repositories", "workspaces")
    op.drop_column("workspaces", "last_indexed_at")

    op.alter_column(
        "agent_runs",
        "repository_id",
        new_column_name="workspace_id",
    )
    op.add_column("agent_runs", sa.Column("workspace_root", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE agent_runs AS runs
        SET workspace_root = workspaces.root_path
        FROM workspaces
        WHERE runs.workspace_id = workspaces.id
        """
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "workspace_root")
    op.alter_column(
        "agent_runs",
        "workspace_id",
        new_column_name="repository_id",
    )
    op.add_column(
        "workspaces",
        sa.Column("last_indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.rename_table("workspaces", "repositories")

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
        sa.Column("scanned_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_files", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index(
        "uq_repository_index_jobs_active_repository",
        "repository_index_jobs",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
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
