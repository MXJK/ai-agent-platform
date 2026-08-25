"""Persist synthetic model latency probes separately from live traffic.

Revision ID: 20260825_0025
Revises: 20260823_0024
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260825_0025"
down_revision = "20260823_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_probe_stats",
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
            "latency_samples_ms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sample_count >= 0", name="ck_model_probe_samples"),
        sa.CheckConstraint("success_count >= 0", name="ck_model_probe_success"),
        sa.CheckConstraint("failure_count >= 0", name="ck_model_probe_failure"),
        sa.CheckConstraint(
            "last_latency_ms IS NULL OR last_latency_ms >= 0",
            name="ck_model_probe_last_latency",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_probe_stats")
