"""Add persistent session metadata and user preferences.

Revision ID: 20260804_0014
Revises: 20260804_0013
Create Date: 2026-08-04 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_0014"
down_revision = "20260804_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_run_model_preferences",
        sa.Column("preferred_provider", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_run_model_preferences",
        sa.Column("preferred_model", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_run_model_preferences",
        sa.Column("thinking_level", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_agent_model_thinking_level",
        "agent_run_model_preferences",
        "thinking_level IS NULL OR thinking_level IN ('minimal', 'low', 'medium', 'high')",
    )
    op.add_column(
        "sessions",
        sa.Column("title", sa.Text(), nullable=False, server_default="新会话"),
    )
    op.add_column(
        "sessions",
        sa.Column("title_source", sa.Text(), nullable=False, server_default="default"),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("sessions", sa.Column("workspace_id", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("provider", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("model", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("thinking_level", sa.Text(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("composer_mode", sa.Text(), nullable=False, server_default="chat"),
    )
    op.create_foreign_key(
        "fk_sessions_workspace_id",
        "sessions",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_sessions_title_source",
        "sessions",
        "title_source IN ('default', 'auto', 'manual')",
    )
    op.create_check_constraint(
        "ck_sessions_composer_mode",
        "sessions",
        "composer_mode IN ('chat', 'agent')",
    )
    op.create_check_constraint(
        "ck_sessions_thinking_level",
        "sessions",
        "thinking_level IS NULL OR thinking_level IN ('minimal', 'low', 'medium', 'high')",
    )

    op.execute(
        """
        UPDATE sessions AS target
        SET title = source.title,
            title_source = source.title_source,
            updated_at = source.updated_at
        FROM (
            SELECT sessions.id,
                   COALESCE(
                       NULLIF(
                           LEFT(
                               REGEXP_REPLACE(first_user.content, '\\s+', ' ', 'g'),
                               48
                           ),
                           ''
                       ),
                       '新会话'
                   ) AS title,
                   CASE WHEN first_user.content IS NULL THEN 'default' ELSE 'auto' END
                       AS title_source,
                   GREATEST(
                       sessions.created_at,
                       COALESCE(last_message.created_at, sessions.created_at)
                   ) AS updated_at
            FROM sessions
            LEFT JOIN LATERAL (
                SELECT content
                FROM messages
                WHERE messages.session_id = sessions.id
                  AND messages.role = 'user'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            ) AS first_user ON TRUE
            LEFT JOIN LATERAL (
                SELECT created_at
                FROM messages
                WHERE messages.session_id = sessions.id
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            ) AS last_message ON TRUE
        ) AS source
        WHERE target.id = source.id
        """
    )

    op.create_index(
        "idx_sessions_user_archive_updated",
        "sessions",
        ["user_id", "archived_at", "updated_at", "id"],
    )

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("default_provider", sa.Text(), nullable=True),
        sa.Column("default_model", sa.Text(), nullable=True),
        sa.Column("default_thinking_level", sa.Text(), nullable=True),
        sa.Column(
            "default_workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "default_composer_mode",
            sa.Text(),
            nullable=False,
            server_default="chat",
        ),
        sa.Column(
            "last_active_session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "default_composer_mode IN ('chat', 'agent')",
            name="ck_user_preferences_composer_mode",
        ),
        sa.CheckConstraint(
            "default_thinking_level IS NULL OR "
            "default_thinking_level IN ('minimal', 'low', 'medium', 'high')",
            name="ck_user_preferences_thinking_level",
        ),
    )

    op.execute(
        """
        INSERT INTO user_preferences (user_id, last_active_session_id)
        SELECT DISTINCT ON (user_id) user_id, id
        FROM sessions
        ORDER BY user_id, updated_at DESC, id DESC
        ON CONFLICT (user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_index("idx_sessions_user_archive_updated", table_name="sessions")
    op.drop_constraint("ck_sessions_thinking_level", "sessions", type_="check")
    op.drop_constraint("ck_sessions_composer_mode", "sessions", type_="check")
    op.drop_constraint("ck_sessions_title_source", "sessions", type_="check")
    op.drop_constraint("fk_sessions_workspace_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "composer_mode")
    op.drop_column("sessions", "thinking_level")
    op.drop_column("sessions", "model")
    op.drop_column("sessions", "provider")
    op.drop_column("sessions", "workspace_id")
    op.drop_column("sessions", "archived_at")
    op.drop_column("sessions", "updated_at")
    op.drop_column("sessions", "title_source")
    op.drop_column("sessions", "title")
    op.drop_constraint(
        "ck_agent_model_thinking_level",
        "agent_run_model_preferences",
        type_="check",
    )
    op.drop_column("agent_run_model_preferences", "thinking_level")
    op.drop_column("agent_run_model_preferences", "preferred_model")
    op.drop_column("agent_run_model_preferences", "preferred_provider")
