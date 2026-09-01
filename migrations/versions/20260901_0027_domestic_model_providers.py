"""Allow domestic providers in the global model registry.

Revision ID: 20260901_0027
Revises: 20260831_0026
"""

from alembic import op


revision = "20260901_0027"
down_revision = "20260831_0026"
branch_labels = None
depends_on = None


_CONSTRAINT_NAME = "ck_model_connections_provider_supported"
_TABLE_NAME = "model_provider_connections"
_LEGACY_PROVIDERS = (
    "provider IN ('anthropic', 'deepseek', 'fake', 'google', 'openai')"
)
_SUPPORTED_PROVIDERS = (
    "provider IN ('anthropic', 'deepseek', 'doubao', 'fake', 'glm', "
    "'google', 'minimax', 'openai')"
)


def upgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        _SUPPORTED_PROVIDERS,
    )


def downgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        _LEGACY_PROVIDERS,
    )
