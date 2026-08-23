"""Version eval records and key baselines by their compatibility tuple.

Revision ID: 20260823_0024
Revises: 20260822_0023
"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_0024"
down_revision = "20260822_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows intentionally become legacy data. They remain readable but
    # cannot silently compare with evaluator 2.0 runs.
    op.add_column(
        "eval_runs",
        sa.Column(
            "evaluator_version",
            sa.Text(),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "eval_runs",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_eval_runs_compat_started",
        "eval_runs",
        ["provider", "model", "suite_id", "evaluator_version", "started_at"],
    )

    op.add_column(
        "eval_baselines",
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "eval_baselines",
        sa.Column("suite_id", sa.Text(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "eval_baselines",
        sa.Column(
            "evaluator_version",
            sa.Text(),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "eval_baselines",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "eval_baselines",
        sa.Column(
            "forced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.drop_constraint("eval_baselines_pkey", "eval_baselines", type_="primary")
    op.create_primary_key(
        "eval_baselines_pkey",
        "eval_baselines",
        ["provider", "model", "suite_id", "evaluator_version"],
    )


def downgrade() -> None:
    # The legacy schema can represent only one row per provider. Refuse an
    # unsafe downgrade instead of silently discarding model/suite baselines.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT provider
                    FROM eval_baselines
                    GROUP BY provider
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'cannot downgrade eval baselines: multiple compatibility keys exist for one provider';
                END IF;
            END
            $$
            """
        )
    )
    op.drop_constraint("eval_baselines_pkey", "eval_baselines", type_="primary")
    op.create_primary_key("eval_baselines_pkey", "eval_baselines", ["provider"])
    op.drop_column("eval_baselines", "forced")
    op.drop_column("eval_baselines", "schema_version")
    op.drop_column("eval_baselines", "evaluator_version")
    op.drop_column("eval_baselines", "suite_id")
    op.drop_column("eval_baselines", "model")
    op.drop_index("ix_eval_runs_compat_started", table_name="eval_runs")
    op.drop_column("eval_runs", "schema_version")
    op.drop_column("eval_runs", "evaluator_version")
