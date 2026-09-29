# Source and adaptation

The team configuration, mailbox, structured-message and shared-task semantics
in this package are adapted from the user-supplied local
`mewcode-python/mewcode/teams/` snapshot.

Cogent uses server-owned `~/.cogent/teams` storage, atomic JSON replacement and
in-process teammates backed by persisted child Runs. It deliberately omits the
reference tmux/iTerm2 pane launchers because this project is an API/worker
service and must preserve its process, authentication and Workspace boundaries.

The supplied snapshot contains no LICENSE file. This provenance note records
the implementation source and does not grant an external distribution license.
