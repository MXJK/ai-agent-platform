# Source and adaptation

The slug, change-detection, recovery and clean-versus-dirty cleanup semantics in
this package are adapted from the user-supplied local
`mewcode-python/mewcode/worktree/` snapshot.

The platform implementation uses a server-owned parent directory, noninteractive
Git commands, repository-scoped hashed paths, the existing `codex/` branch
prefix, and never copies `.env` files or symlinks dependency directories into a
new worktree. Current Cogent Run execution roots remain immutable; named
worktrees are used for isolated Subagents and Agent Team members.

The supplied snapshot contains no LICENSE file. This provenance note records
the implementation source and does not grant an external distribution license.
