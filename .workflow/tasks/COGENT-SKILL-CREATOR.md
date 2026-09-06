# COGENT-SKILL-CREATOR: Add Cogent Skill creator workflow

## Goal

Implement a bundled Skill creator adapted from the local mewcode reference, with safe project-local scaffolding, validation, packaging, documentation, and tests.

## In scope

- Add a bundled inline `skill-creator` that creates or improves Cogent Skills
  under the current Workspace's `.cogent/skills/` directory.
- Add deterministic project-local initialization, structural validation, and
  `.skill` packaging helpers adapted to the current Cogent schema and safety
  boundary.
- Accept harmless standard Skill metadata used by creator-authored packages
  without treating it as tool authority or execution policy.
- Cover discovery, creation, validation, packaging, symlink rejection, and
  incomplete scaffold behavior with focused tests.
- Synchronize README and the interview handbook capability evidence.

## Out of scope

- Remote Skill download or installation, user-global writes, external model
  calls, deployment, migration, commit, or push.
- Fork/subagent Skill execution and the mewcode subagent benchmark/viewer loop.
- Executing scripts merely because they are present in a Skill package.

## Acceptance criteria

- [x] `bundled:skill-creator` is discoverable and available as
      `/skill-creator` without granting additional tools.
- [x] The initializer creates a valid project-local `.cogent/skills/<name>`
      package atomically and refuses to overwrite an existing Skill.
- [x] Validation rejects malformed frontmatter, name/directory mismatches,
      unfinished placeholders, symlinks, and unsafe package paths.
- [x] Packaging validates first, excludes evaluation/build debris, rejects
      symlinks, and emits a deterministic `.skill` archive outside the source.
- [x] Optional `metadata`, `compatibility`, and `license` frontmatter are
      accepted as inert descriptive data; permission and tool boundaries stay
      unchanged.
- [x] Relevant focused tests, the full required verification commands, and
      documentation validation pass, or unrelated pre-existing failures are
      recorded precisely.

## Decisions

- Reuse the mewcode creator's useful shape—intent capture, concise SKILL.md,
  progressive disclosure, validation before packaging—but implement helpers
  inside the Cogent package so they work in local and installed environments.
- Keep evaluation guidance inline and evidence-based; do not expose unusable
  subagent or external-CLI workflows in the bundled Skill.
- Supporting resources remain ordinary files inside project-local Skill
  directories. Discovery reads only the declarative entrypoint and never
  executes package contents.

## Verification

- `.venv/bin/python -m pytest -q tests/test_skill_creator.py tests/test_skill_discovery.py tests/test_skill_registry.py tests/test_execution_context.py tests/test_api.py -k 'skill or composer_capabilities'`
  - Passed: 23 tests; 58 deselected; one existing Starlette deprecation warning.
- Real CLI smoke in a temporary Workspace:
  - `init` created `.cogent/skills/smoke-skill` with optional resources.
  - `validate` returned `valid: true`.
  - `package` emitted `smoke-skill.skill` outside the source directory.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - Passed: 24 Markdown files and 45 capabilities; the validator reported its
    normal evidence-review warnings for a dirty working tree.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - Passed.
- `node --check ai_agent_platform/static/app.js && node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`
  - Passed: 26 tests.
- `git diff --check`
  - Passed.
- `.venv/bin/python -m pytest -q`
  - Repository-wide result: 880 passed, 12 skipped, 111 subtests passed, and
    one failure.
  - The sole failure is the pre-existing
    `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`:
    the test expects `OSError`, while `ManagedFiles.read` normalizes the
    symlinked-parent failure to `ValueError`. No Skill creator file participates
    in this failure.

## Result

Implemented the bundled inline creator, project-local deterministic CLI,
frontmatter compatibility, tests, and user/developer documentation. The useful
shape from mewcode's Apache-2.0 `skill-creator` was adapted to Cogent's existing
Skill schema and Workspace safety boundary; its fork/subagent evaluator,
external model calls, remote installation behavior, and branding were not
copied.

The feature-specific acceptance evidence is complete. The workflow task remains
blocked only because the repository's required full-suite check is not green due
to the unrelated file-memory exception-contract failure recorded above.
