from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from ai_agent_platform.skills import SkillDiscovery
from ai_agent_platform.skills.service import SkillService
from ai_agent_platform.skills.creator import (
    SkillCreatorError,
    initialize_skill,
    main,
    package_skill,
    validate_skill_package,
)


class SkillCreatorTests(unittest.TestCase):
    def test_bundled_creator_is_discoverable_as_a_slash_command(self) -> None:
        package_root = Path(__file__).parents[1] / "ai_agent_platform"
        catalog = SkillDiscovery(
            bundled_root=package_root / "bundled_skills"
        ).discover()

        skill = catalog.get_skill("skill-creator")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.qualified_name, "bundled:skill-creator")
        self.assertEqual(
            catalog.resolve_command("/skill-creator").skill_qualified_name,
            "bundled:skill-creator",
        )
        self.assertIn("sandbox.write_file", skill.required_tools)

        service = SkillService(
            SkillDiscovery(bundled_root=package_root / "bundled_skills"),
            enabled=True,
        )
        selection = service.build_context(
            workspace_root=package_root,
            agent="coding",
            mode="default",
            max_chars=16_000,
            available_tools=skill.required_tools,
            selected_skill_names=("skill-creator",),
            arguments="创建发布说明 Skill",
        )
        self.assertEqual(len(selection.sources), 1)
        self.assertIn("Create Skills", selection.sources[0].text)
        self.assertTrue(selection.sources[0].path.endswith("skill-creator"))

    def test_initializer_creates_complete_project_skill_and_resources(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            created = initialize_skill(
                workspace,
                name="release-notes",
                description="Write evidence-based release notes for a repository change.",
                instructions="Inspect the diff and write concise release notes.",
                resources=("references", "scripts"),
            )

            self.assertEqual(
                created,
                workspace / ".cogent" / "skills" / "release-notes",
            )
            self.assertTrue((created / "references").is_dir())
            self.assertTrue((created / "scripts").is_dir())
            report = validate_skill_package(created)
            self.assertTrue(report.valid, report.as_dict())
            self.assertEqual(report.name, "release-notes")

            catalog = SkillDiscovery().discover(project_root=workspace)
            self.assertEqual(
                catalog.get_skill("release-notes").qualified_name,
                "project:release-notes",
            )

    def test_initializer_refuses_overwrite_and_symlink_escape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            kwargs = {
                "name": "safe-skill",
                "description": "Perform a bounded safe workflow.",
                "instructions": "Perform the requested bounded workflow.",
            }
            initialize_skill(workspace, **kwargs)
            with self.assertRaisesRegex(SkillCreatorError, "already exists"):
                initialize_skill(workspace, **kwargs)

        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as outside_dir:
            workspace = Path(temp_dir)
            (workspace / ".cogent").symlink_to(
                Path(outside_dir), target_is_directory=True
            )
            with self.assertRaisesRegex(SkillCreatorError, "symbolic links"):
                initialize_skill(
                    workspace,
                    name="escaped",
                    description="Must remain in the Workspace.",
                    instructions="Stay inside the Workspace.",
                )

    def test_validation_rejects_placeholders_name_mismatch_and_symlinks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "wrong-directory"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\n"
                "name: actual-name\n"
                "description: Improve a repeatable workflow.\n"
                "---\n"
                "[TODO: finish these instructions]\n",
                encoding="utf-8",
            )
            (root / "linked.txt").symlink_to(root / "SKILL.md")

            report = validate_skill_package(root)

            self.assertFalse(report.valid)
            self.assertEqual(
                {issue.code for issue in report.issues},
                {"name_mismatch", "path_symlink", "unfinished_placeholder"},
            )

    def test_validation_rejects_malformed_frontmatter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir) / "broken-frontmatter"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: [unterminated\n---\nDo the work.\n",
                encoding="utf-8",
            )

            report = validate_skill_package(skill)

            self.assertFalse(report.valid)
            self.assertTrue(
                any(issue.path == "SKILL.md" for issue in report.issues),
                report.as_dict(),
            )

    def test_standard_metadata_is_inert_and_does_not_grant_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill = root / "creator-compatible"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: creator-compatible\n"
                "description: Demonstrate harmless creator metadata compatibility.\n"
                "metadata:\n"
                "  short-description: Creator compatible\n"
                "compatibility: Cogent inline Skills\n"
                "license: Apache-2.0\n"
                "---\n"
                "Follow the requested workflow.\n",
                encoding="utf-8",
            )

            report = validate_skill_package(skill)
            catalog = SkillDiscovery(user_root=root).discover()

            self.assertTrue(report.valid, report.as_dict())
            definition = catalog.get_skill("creator-compatible")
            self.assertIsNotNone(definition)
            self.assertEqual(definition.required_tools, ())

    def test_packaging_is_deterministic_and_excludes_eval_debris(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_one = Path(temp_dir) / "dist-one"
            output_two = Path(temp_dir) / "dist-two"
            workspace.mkdir()
            skill = initialize_skill(
                workspace,
                name="pack-me",
                description="Package a deterministic test Skill.",
                instructions="Produce the requested deterministic output.",
                resources=("references",),
            )
            (skill / "references" / "format.md").write_text(
                "# Format\n\nUse concise Markdown.\n", encoding="utf-8"
            )
            (skill / "evals").mkdir()
            (skill / "evals" / "evals.json").write_text("{}", encoding="utf-8")

            first = package_skill(skill, output_directory=output_one)
            second = package_skill(skill, output_directory=output_two)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "pack-me/SKILL.md",
                        "pack-me/references/format.md",
                    ],
                )
            with self.assertRaisesRegex(SkillCreatorError, "already exists"):
                package_skill(skill, output_directory=output_one)
            with self.assertRaisesRegex(SkillCreatorError, "outside"):
                package_skill(skill, output_directory=skill / "dist")

    def test_packaging_rejects_a_symlinked_skill_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            skill = initialize_skill(
                workspace,
                name="real-skill",
                description="Provide a real package for a symlink test.",
                instructions="Perform the real workflow.",
            )
            link = root / "linked-skill"
            link.symlink_to(skill, target_is_directory=True)

            with self.assertRaisesRegex(SkillCreatorError, "symbolic link"):
                package_skill(link, output_directory=root / "dist")

    def test_cli_validate_returns_machine_readable_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir) / "broken"
            skill.mkdir()
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["validate", str(skill)])

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["valid"])
            self.assertEqual(payload["issues"][0]["code"], "missing_entrypoint")


if __name__ == "__main__":
    unittest.main()
