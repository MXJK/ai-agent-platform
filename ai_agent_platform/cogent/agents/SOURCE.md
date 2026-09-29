# Source and adaptation

The Agent-definition frontmatter, project/user/built-in precedence, conversation
fork and background-task semantics in this package are adapted from the
user-supplied local `mewcode-python/mewcode/agents/` snapshot.

Cogent maps every worker to a persisted child Run and intersects its requested
tools and permission mode with the parent's frozen platform boundaries. It does
not fabricate missing Provider tool results or allow ordinary Subagents to
spawn nested Subagents.

The supplied snapshot contains no LICENSE file. This provenance note records
the implementation source and does not grant an external distribution license.
