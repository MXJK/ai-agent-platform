"""Create initial application tables.

Revision ID: 20260715_0001
Revises:
Create Date: 2026-07-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260715_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_messages_session_created",
        "messages",
        ["session_id", "created_at"],
    )

    op.create_table(
        "token_usage_records",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_token_usage_session_created",
        "token_usage_records",
        ["session_id", "created_at"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("repository_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=True),
        sa.Column("latest_node", sa.Text(), nullable=True),
        sa.Column(
            "next_nodes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "trace",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
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
    )
    op.create_index(
        "idx_agent_runs_conversation_created",
        "agent_runs",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "idx_agent_runs_status_updated",
        "agent_runs",
        ["status", "updated_at"],
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_documents_kb_created",
        "documents",
        ["knowledge_base_id", "created_at"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("qdrant_point_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_document_chunks_kb_file",
        "document_chunks",
        ["knowledge_base_id", "filename"],
    )


def downgrade() -> None:
    op.drop_index("idx_document_chunks_kb_file", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("idx_documents_kb_created", table_name="documents")
    op.drop_table("documents")
    op.drop_index("idx_agent_runs_status_updated", table_name="agent_runs")
    op.drop_index("idx_agent_runs_conversation_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(
        "idx_token_usage_session_created",
        table_name="token_usage_records",
    )
    op.drop_table("token_usage_records")
    op.drop_index("idx_messages_session_created", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
