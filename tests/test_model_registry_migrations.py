from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260901_0027_domestic_model_providers.py"
)


def _load_migration():
    spec = spec_from_file_location("domestic_model_providers_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_domestic_provider_migration_expands_and_restores_constraint() -> None:
    migration = _load_migration()
    migration.op = Mock()

    migration.upgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "ck_model_connections_provider_supported",
        "model_provider_connections",
        type_="check",
    )
    migration.op.create_check_constraint.assert_called_once_with(
        "ck_model_connections_provider_supported",
        "model_provider_connections",
        "provider IN ('anthropic', 'deepseek', 'doubao', 'fake', 'glm', "
        "'google', 'minimax', 'openai')",
    )

    migration.op.reset_mock()
    migration.downgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "ck_model_connections_provider_supported",
        "model_provider_connections",
        type_="check",
    )
    migration.op.create_check_constraint.assert_called_once_with(
        "ck_model_connections_provider_supported",
        "model_provider_connections",
        "provider IN ('anthropic', 'deepseek', 'fake', 'google', 'openai')",
    )
