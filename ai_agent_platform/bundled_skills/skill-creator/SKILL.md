---
name: skill-creator
description: Create, update, validate, or package Cogent Skills. Use when the user wants to turn a repeatable workflow into a Skill, improve an existing SKILL.md, add Skill resources, test Skill behavior, or build a distributable .skill archive.
agents: [coding]
modes: [default]
context_budget: 12000
tools: [repo.read_file, repo.list_files, repo.search_code, sandbox.write_file, sandbox.apply_patch, sandbox.run_command, sandbox.git_diff]
command:
  name: skill-creator
  description: Create or improve a Cogent Skill.
  usage: /skill-creator <goal or existing Skill path>
---
# Skill Creator

Create Skills that add useful, non-obvious workflow guidance without widening the user's request or authority.

## Choose the target

- Create project Skills under `.cogent/skills/<skill-name>/SKILL.md` in the current execution Workspace.
- Update an existing Skill in place only after reading its entrypoint and relevant resources.
- Do not write to a user-global directory, download a remote Skill, or install dependencies unless the user explicitly requests that separate action and the runtime authorizes it.
- Preserve the existing Skill name during updates unless the user explicitly asks to rename it.

## Capture intent

Use the current conversation and repository evidence before asking questions. Resolve these points when they materially affect the result:

1. What repeated task should the Skill enable?
2. Which user intents should and should not select it?
3. What output or observable behavior defines success?
4. Which tools or local resources are genuinely required?

Ask only for a user-owned choice that cannot be inferred safely.

## Write the package

Keep the entrypoint focused and disclose conditional detail progressively:

```text
.cogent/skills/<skill-name>/
|-- SKILL.md
|-- scripts/       optional deterministic helpers
|-- references/    optional task-specific documentation
`-- assets/        optional output templates or media
```

The `SKILL.md` frontmatter requires `name` and `description`. Cogent also supports `agents`, `modes`, `context_budget`, `tools`, `command`, and inert descriptive `metadata`, `compatibility`, and `license` fields. Skill metadata never grants tools or bypasses permissions.

Use a short lowercase kebab-case name of at most 64 characters. Make the description precise enough to distinguish genuine matches from adjacent requests; put detailed steps in the body. Prefer imperative instructions and explain constraints that change decisions. Avoid generic advice, copied manuals, speculative edge cases, duplicated documentation, and placeholders in a finished Skill.

Create `scripts/`, `references/`, or `assets/` only when they have a concrete role. Link each reference from `SKILL.md` and state when it should be read. A script is data until the Agent explicitly invokes it through the normal command and approval boundary.

When command execution is available, the deterministic initializer can create a complete package from prepared instructions:

```text
python -m ai_agent_platform.skills.creator init <name> --workspace . --description <description> --instructions-file <workspace-file> [--resource scripts|references|assets]
```

Otherwise create the files with the normal Workspace write tools. Never overwrite an existing Skill with the initializer; read and edit it deliberately.

## Validate

Run structural validation after creating or changing the package:

```text
python -m ai_agent_platform.skills.creator validate .cogent/skills/<skill-name>
```

Validation must pass before presenting the Skill as complete. It checks the runtime frontmatter contract, directory/name agreement, unfinished placeholders, UTF-8, file types, package size, and symbolic-link boundaries. Also run any new helper script against a representative safe input.

For behavior testing, use two or three realistic prompts that differ in wording and include a near-miss that should not select the Skill. Compare observable outputs and tool use with the user's acceptance criteria. Do not make paid Provider calls, external writes, or repeated evaluation runs without the required authorization and a bounded stopping condition.

## Package when requested

Create a distributable archive only after validation:

```text
python -m ai_agent_platform.skills.creator package .cogent/skills/<skill-name> --output-directory <workspace-output-directory>
```

The output directory must be outside the Skill source. Packaging excludes evaluation and build debris, rejects symbolic links, and refuses to replace an existing archive.

## Finish

Report the Skill path, what causes it to be selected, resources added, validation performed, behavior tests run, and any unverified assumptions. Do not claim that a structurally valid Skill improves results unless behavior evidence supports that conclusion.
