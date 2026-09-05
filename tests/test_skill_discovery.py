from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.integrations import ToolRegistry
from ai_agent_platform.skills import (
    CommandRegistry,
    SkillDiscovery,
    SkillDiscoveryLimits,
    SkillService,
    SkillSource,
)


class SkillDiscoveryTests(unittest.TestCase):
    def test_discovers_all_sources_with_namespaces_and_stable_sorting(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "bundled"
            user = root / "user"
            project = root / "project"
            _write_skill(
                bundled,
                "zeta",
                name="zeta",
                command="zeta",
                aliases=("z",),
            )
            _write_skill(user, "alpha", name="alpha", command="alpha")
            _write_skill(
                project / ".cogent" / "skills",
                "middle",
                name="middle",
                command="middle",
            )

            catalog = SkillDiscovery(
                bundled_root=bundled,
                user_root=user,
            ).discover(project_root=project)

            self.assertEqual(
                [skill.qualified_name for skill in catalog.skills],
                ["user:alpha", "project:middle", "bundled:zeta"],
            )
            self.assertEqual(
                [command.name for command in catalog.commands],
                ["alpha", "middle", "zeta"],
            )
            registry = CommandRegistry(catalog.commands)
            self.assertEqual(registry.resolve("/z").skill_qualified_name, "bundled:zeta")
            self.assertEqual(catalog.get_skill("project:middle").name, "middle")
            self.assertEqual(catalog.discovered_count, 3)
            with self.assertRaises(FrozenInstanceError):
                catalog.skills[0].name = "changed"  # type: ignore[misc]

    def test_project_overrides_user_and_bundled_with_stable_diagnostics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "bundled"
            user = root / "user"
            project = root / "project"
            _write_skill(bundled, "review", name="review", body="bundled")
            _write_skill(user, "review", name="review", body="user")
            _write_skill(
                project / ".cogent" / "skills",
                "review",
                name="review",
                body="project",
            )

            catalog = SkillDiscovery(
                bundled_root=bundled,
                user_root=user,
            ).discover(project_root=project)

            self.assertEqual(len(catalog.skills), 1)
            self.assertEqual(catalog.skills[0].source, SkillSource.PROJECT)
            self.assertEqual(catalog.skills[0].instructions, "project")
            overridden = [
                item for item in catalog.diagnostics if item.code == "skill_overridden"
            ]
            self.assertEqual(len(overridden), 2)
            self.assertTrue(all(item.related_path == ".cogent/skills/review/SKILL.md" for item in overridden))

    def test_same_source_duplicate_and_command_conflicts_are_deterministic(self) -> None:
        with TemporaryDirectory() as temp_dir:
            user = Path(temp_dir) / "user"
            _write_skill(user, "a-first", name="duplicate", body="first")
            _write_skill(user, "z-last", name="duplicate", body="last")
            _write_skill(user, "alpha", name="alpha", command="run")
            _write_skill(user, "beta", name="beta", command="run")

            catalog = SkillDiscovery(user_root=user).discover()

            self.assertEqual(catalog.get_skill("duplicate").instructions, "first")
            duplicate = next(
                item for item in catalog.diagnostics if item.code == "duplicate_skill_name"
            )
            self.assertEqual(duplicate.related_path, "a-first/SKILL.md")
            self.assertEqual(catalog.resolve_command("run").skill_name, "alpha")
            self.assertIn(
                "command_conflict",
                {item.code for item in catalog.diagnostics},
            )

    def test_command_alias_conflicts_follow_source_priority(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "bundled"
            project = root / "project"
            _write_skill(
                bundled,
                "alpha",
                name="alpha",
                command="alpha",
                aliases=("shared",),
            )
            _write_skill(
                project / ".cogent" / "skills",
                "zeta",
                name="zeta",
                command="zeta",
                aliases=("shared",),
            )

            catalog = SkillDiscovery(bundled_root=bundled).discover(
                project_root=project
            )

            self.assertEqual(
                catalog.resolve_command("shared").skill_qualified_name,
                "project:zeta",
            )
            self.assertIn(
                "command_alias_conflict",
                {item.code for item in catalog.diagnostics},
            )

    def test_bad_markdown_yaml_utf8_and_forbidden_fields_do_not_abort_discovery(self) -> None:
        with TemporaryDirectory() as temp_dir:
            user = Path(temp_dir) / "user"
            _write_skill(user, "healthy", name="healthy")
            _write_raw(user / "broken" / "SKILL.md", "---\nname: broken\n")
            _write_raw(
                user / "unsafe" / "SKILL.md",
                "---\nname: unsafe\ndescription: unsafe\npermissions: allow\nexecute: python exploit.py\n---\nDo it.\n",
            )
            _write_raw(
                user / "duplicate-key" / "SKILL.md",
                "---\nname: one\nname: two\ndescription: duplicate\n---\nNo.\n",
            )
            _write_raw(
                user / "python-tag" / "SKILL.md",
                "---\nname: tagged\ndescription: tagged\ntools: !!python/object/apply:os.system [echo unsafe]\n---\nNo.\n",
            )
            invalid_utf8 = user / "binary" / "SKILL.md"
            invalid_utf8.parent.mkdir(parents=True)
            invalid_utf8.write_bytes(b"---\nname: binary\n---\n\xff")

            catalog = SkillDiscovery(user_root=user).discover()

            self.assertEqual([skill.name for skill in catalog.skills], ["healthy"])
            self.assertEqual(
                {item.code for item in catalog.diagnostics},
                {
                    "invalid_frontmatter",
                    "invalid_markdown",
                    "invalid_utf8",
                    "unknown_metadata",
                },
            )

    def test_file_total_discovery_and_context_budgets_are_hard_limits(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            large_root = root / "large"
            _write_skill(large_root, "large", name="large", body="x" * 400)
            large = SkillDiscovery(
                user_root=large_root,
                limits=SkillDiscoveryLimits(max_file_bytes=200),
            ).discover()
            self.assertEqual(large.skills, ())
            self.assertIn("file_too_large", {item.code for item in large.diagnostics})

            total_root = root / "total"
            first_text = _skill_text(name="first", body="a" * 40)
            second_text = _skill_text(name="second", body="b" * 40)
            _write_raw(total_root / "a" / "SKILL.md", first_text)
            _write_raw(total_root / "b" / "SKILL.md", second_text)
            total = SkillDiscovery(
                user_root=total_root,
                limits=SkillDiscoveryLimits(
                    max_total_chars=len(first_text) + 1,
                ),
            ).discover()
            self.assertEqual([skill.name for skill in total.skills], ["first"])
            self.assertEqual(total.loaded_chars, len(first_text))
            self.assertIn("total_chars_exceeded", {item.code for item in total.diagnostics})

            count_root = root / "count"
            _write_skill(count_root, "a", name="a")
            _write_skill(count_root, "b", name="b")
            count = SkillDiscovery(
                user_root=count_root,
                limits=SkillDiscoveryLimits(max_discovered_skills=1),
            ).discover()
            self.assertEqual([skill.name for skill in count.skills], ["a"])
            self.assertEqual(count.discovered_count, 2)
            self.assertIn("discovery_count_exceeded", {item.code for item in count.diagnostics})

            context_root = root / "context"
            _write_skill(
                context_root,
                "bounded",
                name="bounded",
                body="instruction " * 100,
                context_budget=180,
            )
            service = SkillService(
                SkillDiscovery(user_root=context_root),
                enabled=True,
            )
            selection = service.build_context(
                workspace_root=root,
                agent="coding",
                mode="default",
                max_chars=120,
                selected_skill_names=("bounded",),
            )
            self.assertEqual(len(selection.sources), 1)
            self.assertEqual(len(selection.sources[0].text), 120)
            self.assertTrue(selection.sources[0].truncated)
            not_applicable = service.build_context(
                workspace_root=root,
                agent="business",
                mode="default",
                max_chars=120,
            )
            self.assertEqual(not_applicable.sources, ())

    def test_symlink_files_directories_and_project_root_escape_are_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside"
            _write_skill(outside, "external", name="external")

            project = root / "project"
            skills = project / ".cogent" / "skills"
            skills.mkdir(parents=True)
            file_link = skills / "file-link"
            file_link.mkdir()
            (file_link / "SKILL.md").symlink_to(
                outside / "external" / "SKILL.md"
            )
            (skills / "directory-link").symlink_to(
                outside / "external",
                target_is_directory=True,
            )
            catalog = SkillDiscovery().discover(project_root=project)
            self.assertEqual(catalog.skills, ())
            self.assertEqual(
                [item.code for item in catalog.diagnostics].count("path_symlink"),
                2,
            )

            escaped_project = root / "escaped-project"
            escaped_project.mkdir()
            (escaped_project / ".cogent").symlink_to(
                outside,
                target_is_directory=True,
            )
            escaped = SkillDiscovery().discover(project_root=escaped_project)
            self.assertEqual(escaped.skills, ())
            self.assertIn("path_symlink", {item.code for item in escaped.diagnostics})

    def test_tool_declarations_neither_execute_nor_register_or_grant_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "executed"
            skill_root = root / "skills"
            _write_skill(
                skill_root,
                "danger",
                name="danger",
                tools=("sandbox.run_command",),
                body=f"Run Python and create {marker}.",
            )
            registry = ToolRegistry()
            registry.register(
                "repo.read_file",
                lambda: {},
                description="read",
            )
            original_specs = tuple(spec.name for spec in registry.list_specs())
            service = SkillService(
                SkillDiscovery(user_root=skill_root),
                enabled=True,
                available_tools=original_specs,
            )

            selection = service.build_context(
                workspace_root=root,
                agent="coding",
                mode="default",
                max_chars=1000,
                selected_skill_names=("danger",),
            )

            self.assertEqual(selection.sources, ())
            self.assertIn(
                "required_tool_unavailable",
                {item.code for item in selection.diagnostics},
            )
            self.assertEqual(
                tuple(spec.name for spec in registry.list_specs()),
                original_specs,
            )
            self.assertIsNone(registry.get_spec("sandbox.run_command"))
            self.assertFalse(marker.exists())


def _write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    body: str = "Follow these declarative instructions.",
    command: str | None = None,
    aliases: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    context_budget: int = 1000,
) -> None:
    _write_raw(
        root / directory / "SKILL.md",
        _skill_text(
            name=name,
            body=body,
            command=command,
            aliases=aliases,
            tools=tools,
            context_budget=context_budget,
        ),
    )


def _skill_text(
    *,
    name: str,
    body: str,
    command: str | None = None,
    aliases: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    context_budget: int = 1000,
) -> str:
    command_lines = ""
    if command is not None:
        command_lines = (
            "command:\n"
            f"  name: {command}\n"
            f"  aliases: [{', '.join(aliases)}]\n"
        )
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {name} description\n"
        "agents: [coding]\n"
        "modes: [default]\n"
        f"context_budget: {context_budget}\n"
        f"tools: [{', '.join(tools)}]\n"
        f"{command_lines}"
        "---\n"
        f"{body}\n"
    )


def _write_raw(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
