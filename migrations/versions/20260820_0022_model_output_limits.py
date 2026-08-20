"""Add per-model maximum output token capabilities.

Revision ID: 20260820_0022
Revises: 20260813_0021
"""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0022"
down_revision = "20260813_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registered_models",
        sa.Column(
            "max_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="16384",
        ),
    )
    op.execute(
        "UPDATE registered_models SET max_output_tokens = 8192 "
        "WHERE provider = 'deepseek'"
    )
    op.execute(
        "UPDATE registered_models SET max_output_tokens = 4096 "
        "WHERE provider = 'fake'"
    )
    op.create_check_constraint(
        "ck_registered_models_max_output",
        "registered_models",
        "max_output_tokens > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_registered_models_max_output",
        "registered_models",
        type_="check",
    )
    op.drop_column("registered_models", "max_output_tokens")
