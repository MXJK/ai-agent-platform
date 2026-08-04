"""Add global model registry, session preferences, and passive telemetry.

Revision ID: 20260804_0013
Revises: 20260731_0012
Create Date: 2026-08-04 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260804_0013"
down_revision = "20260731_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_provider_connections",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(provider) > 0", name="ck_model_connections_provider"),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'deepseek', 'fake', 'google', 'openai')",
            name="ck_model_connections_provider_supported",
        ),
        sa.CheckConstraint(
            "secret_ref IS NULL OR length(secret_ref) > 0",
            name="ck_model_connections_secret_ref",
        ),
    )
    op.create_table(
        "registered_models",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "provider",
            sa.Text(),
            sa.ForeignKey("model_provider_connections.provider", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("context_window_tokens", sa.BigInteger(), nullable=False),
        sa.Column("tool_calling", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("structured_output", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_cost_per_million", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_cost_per_million", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("configured_latency_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "model", name="uq_registered_models_provider_model"),
        sa.CheckConstraint("context_window_tokens > 0", name="ck_registered_models_context"),
        sa.CheckConstraint("input_cost_per_million >= 0", name="ck_registered_models_input_cost"),
        sa.CheckConstraint("output_cost_per_million >= 0", name="ck_registered_models_output_cost"),
        sa.CheckConstraint("quality_score BETWEEN 0 AND 1", name="ck_registered_models_quality"),
        sa.CheckConstraint("configured_latency_ms > 0", name="ck_registered_models_latency"),
    )
    op.create_index(
        "idx_registered_models_provider_enabled",
        "registered_models",
        ["provider", "enabled"],
    )
    op.create_table(
        "session_model_preferences",
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("routing_policy", sa.Text(), nullable=False, server_default="smart"),
        sa.Column(
            "preferred_model_id",
            sa.Text(),
            sa.ForeignKey("registered_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fallback_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('auto', 'manual')", name="ck_session_model_mode"),
        sa.CheckConstraint(
            "routing_policy IN ('smart', 'quality', 'cost', 'latency')",
            name="ck_session_model_policy",
        ),
    )
    op.create_table(
        "agent_run_model_preferences",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("routing_policy", sa.Text(), nullable=False),
        sa.Column(
            "preferred_model_id",
            sa.Text(),
            sa.ForeignKey("registered_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fallback_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('auto', 'manual')", name="ck_agent_model_mode"),
        sa.CheckConstraint(
            "routing_policy IN ('smart', 'quality', 'cost', 'latency')",
            name="ck_agent_model_policy",
        ),
    )
    op.create_table(
        "model_runtime_stats",
        sa.Column(
            "model_id",
            sa.Text(),
            sa.ForeignKey("registered_models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sample_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "ttft_samples_ms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "total_latency_samples_ms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sample_count >= 0", name="ck_model_stats_samples"),
        sa.CheckConstraint("success_count >= 0", name="ck_model_stats_success"),
        sa.CheckConstraint("failure_count >= 0", name="ck_model_stats_failure"),
    )


def downgrade() -> None:
    op.drop_table("model_runtime_stats")
    op.drop_table("agent_run_model_preferences")
    op.drop_table("session_model_preferences")
    op.drop_index("idx_registered_models_provider_enabled", table_name="registered_models")
    op.drop_table("registered_models")
    op.drop_table("model_provider_connections")
