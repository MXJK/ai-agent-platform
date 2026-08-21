from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app
from ai_agent_platform.skills import SkillDiscovery, SkillRegistryService, SkillService
from ai_agent_platform.skills import SkillLoaderTool
from ai_agent_platform.integrations.permissions import ToolExecutionContext


SKILL_DOCUMENT = """---
name: global-review
description: Review code changes with focused findings.
agents: [coding]
modes: [default]
tools: []
---
Inspect the requested change and report prioritized findings.
"""


class SkillRegistryTests(unittest.TestCase):
    def test_user_global_registry_crud_and_default_slash_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                model_secret_backend="memory",
                skills_enabled=True,
                skills_directory_path=str(root / "skills"),
                mcp_config_path=str(root / "mcp.json"),
            )
            with TestClient(
                create_app(settings=settings),
                client=("127.0.0.1", 50000),
            ) as client:
                created = client.put(
                    "/api/v1/skills/global-review",
                    json={"content": SKILL_DOCUMENT, "enabled": True},
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["command"]["name"], "global-review")

                registry = client.get("/api/v1/skills").json()
                self.assertEqual(registry["root"], str(root / "skills"))
                self.assertEqual(
                    [item["qualified_name"] for item in registry["skills"]],
                    ["user:global-review"],
                )

                disabled = client.patch(
                    "/api/v1/skills/global-review/enabled",
                    json={"enabled": False},
                )
                self.assertFalse(disabled.json()["enabled"])
                self.assertEqual(
                    client.get("/api/v1/skills").json()["skills"][0]["enabled"],
                    False,
                )

                deleted = client.delete("/api/v1/skills/global-review")
                self.assertEqual(deleted.status_code, 204)
                self.assertEqual(client.get("/api/v1/skills").json()["skills"], [])

    def test_skill_service_uses_one_catalog_for_every_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_root = root / "skills"
            document = skill_root / "global-review" / "SKILL.md"
            document.parent.mkdir(parents=True)
            document.write_text(SKILL_DOCUMENT, encoding="utf-8")
            service = SkillService(
                SkillDiscovery(user_root=skill_root),
                enabled=True,
            )
            first = service.discover(workspace_root=root / "alpha")
            second = service.discover(workspace_root=root / "beta")
            self.assertEqual(first.skills, second.skills)
            self.assertEqual(first.commands, second.commands)
            self.assertEqual(first.skills[0].qualified_name, "user:global-review")
            self.assertEqual(
                service.build_context(
                    workspace_root=root / "alpha",
                    agent="coding",
                    mode="default",
                    max_chars=4000,
                ).sources,
                (),
            )

    def test_registry_rejects_name_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = SkillService(
                SkillDiscovery(user_root=Path(temp_dir) / "skills"),
                enabled=True,
            )
            registry = SkillRegistryService(
                user_root=Path(temp_dir) / "skills",
                skill_service=service,
            )
            with self.assertRaisesRegex(ValueError, "route name"):
                registry.upsert("different", content=SKILL_DOCUMENT, enabled=True)

    def test_implicit_loader_returns_one_skill_without_granting_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document = root / "skills" / "global-review" / "SKILL.md"
            document.parent.mkdir(parents=True)
            document.write_text(SKILL_DOCUMENT, encoding="utf-8")
            service = SkillService(
                SkillDiscovery(user_root=root / "skills"),
                enabled=True,
            )
            loader = SkillLoaderTool()
            loader.bind(service)
            result = loader(
                name="user:global-review",
                context=ToolExecutionContext(
                    conversation_id="session",
                    workspace_id="alpha",
                    workspace_root=str(root / "workspace"),
                    process_allowed_tools=("agent.load_skill",),
                    project_allowed_tools=("agent.load_skill",),
                ),
            )
            self.assertEqual(result["name"], "user:global-review")
            self.assertIn("Inspect the requested change", result["instructions"])
            self.assertIn("do not grant tools", result["notice"])


if __name__ == "__main__":
    unittest.main()
