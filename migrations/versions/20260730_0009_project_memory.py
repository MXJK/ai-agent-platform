"""Add workspace-scoped project memory.

Revision ID: 20260730_0009
Revises: 20260727_0008
Create Date: 2026-07-30 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260730_0009"
down_revision = "20260727_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "role IN ('viewer', 'editor', 'admin')",
            name="ck_workspace_members_role",
        ),
    )
    op.create_index(
        "idx_workspace_members_user",
        "workspace_members",
        ["user_id", "workspace_id"],
    )

    op.create_table(
        "workspace_memory_settings",
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "mode IN ('off', 'shadow', 'review', 'auto')",
            name="ck_workspace_memory_settings_mode",
        ),
    )

    op.create_table(
        "project_memories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, search_text)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Text(),
            sa.ForeignKey("project_memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_confirmed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("last_accessed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "access_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
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
        sa.CheckConstraint(
            "kind IN ("
            "'architecture_fact', 'constraint', 'decision', 'convention', "
            "'task_outcome', 'incident_lesson'"
            ")",
            name="ck_project_memories_kind",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'candidate', 'active', 'superseded', 'rejected', 'stale'"
            ")",
            name="ck_project_memories_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_project_memories_confidence",
        ),
        sa.CheckConstraint(
            "importance >= 1 AND importance <= 5",
            name="ck_project_memories_importance",
        ),
    )
    op.create_index(
        "idx_project_memories_workspace_status",
        "project_memories",
        ["workspace_id", "workspace_revision", "status", "updated_at"],
    )
    op.create_index(
        "idx_project_memories_search_vector",
        "project_memories",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "uq_project_memories_active_key",
        "project_memories",
        ["workspace_id", "workspace_revision", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "project_memory_evidence",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "memory_id",
            sa.Text(),
            sa.ForeignKey("project_memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_project_memory_evidence_memory",
        "project_memory_evidence",
        ["memory_id", "created_at"],
    )
    op.create_index(
        "uq_project_memory_evidence_source",
        "project_memory_evidence",
        ["memory_id", "source_kind", "source_id", "path"],
        unique=True,
    )

    op.create_table(
        "memory_extraction_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "candidate_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
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
            "status IN ('pending', 'extracting', 'indexing', 'completed', 'failed')",
            name="ck_memory_extraction_jobs_status",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_id",
            name="uq_memory_extraction_jobs_source",
        ),
    )
    op.create_index(
        "idx_memory_extraction_jobs_workspace",
        "memory_extraction_jobs",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "memory_index_outbox",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
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
        sa.CheckConstraint(
            "operation IN ('upsert', 'delete')",
            name="ck_memory_index_outbox_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_memory_index_outbox_status",
        ),
    )
    op.create_index(
        "idx_memory_index_outbox_status",
        "memory_index_outbox",
        ["status", "created_at"],
    )

    op.create_table(
        "memory_audit_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_memory_audit_workspace_created",
        "memory_audit_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_memory_audit_workspace_created", table_name="memory_audit_events"
    )
    op.drop_table("memory_audit_events")
    op.drop_index("idx_memory_index_outbox_status", table_name="memory_index_outbox")
    op.drop_table("memory_index_outbox")
    op.drop_index(
        "idx_memory_extraction_jobs_workspace",
        table_name="memory_extraction_jobs",
    )
    op.drop_table("memory_extraction_jobs")
    op.drop_index(
        "uq_project_memory_evidence_source",
        table_name="project_memory_evidence",
    )
    op.drop_index(
        "idx_project_memory_evidence_memory",
        table_name="project_memory_evidence",
    )
    op.drop_table("project_memory_evidence")
    op.drop_index("uq_project_memories_active_key", table_name="project_memories")
    op.drop_index(
        "idx_project_memories_search_vector", table_name="project_memories"
    )
    op.drop_index(
        "idx_project_memories_workspace_status", table_name="project_memories"
    )
    op.drop_table("project_memories")
    op.drop_table("workspace_memory_settings")
    op.drop_index("idx_workspace_members_user", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_column("workspaces", "revision")
