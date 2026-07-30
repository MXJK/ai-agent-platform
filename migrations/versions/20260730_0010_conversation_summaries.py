"""Add persistent rolling conversation summaries.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0010"
down_revision = "20260730_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_summaries",
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summarized_message_count", sa.Integer(), nullable=False),
        sa.Column(
            "through_message_id",
            sa.Text(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_chars", sa.Integer(), nullable=False),
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
            "summarized_message_count > 0",
            name="ck_conversation_summaries_message_count",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_conversation_summaries_version",
        ),
        sa.CheckConstraint(
            "source_chars >= 0",
            name="ck_conversation_summaries_source_chars",
        ),
    )
    op.create_index(
        "idx_conversation_summaries_updated",
        "conversation_summaries",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_conversation_summaries_updated",
        table_name="conversation_summaries",
    )
    op.drop_table("conversation_summaries")
