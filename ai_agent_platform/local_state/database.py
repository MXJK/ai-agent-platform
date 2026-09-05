"""Versioned, dependency-free SQLite storage for the local runtime profile."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Iterator

from ai_agent_platform.text_search import fts_index_text


SCHEMA_VERSION = 4


class LocalStateDatabase:
    """Owns schema initialization and short-lived SQLite connections."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self._init_lock = Lock()
        self._initialized = False
        self.fts5_available = False
        self.initialize()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            with self.connect() as conn:
                current = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        "local state schema is newer than this application: "
                        f"{current} > {SCHEMA_VERSION}"
                    )
                if current < 1:
                    conn.executescript(_SCHEMA_V1)
                    self._initialize_fts(conn)
                    conn.execute("PRAGMA user_version = 1")
                    current = 1
                else:
                    self.fts5_available = _table_exists(conn, "messages_fts")
                if current < 2:
                    conn.executescript(_SCHEMA_V2)
                    if self.fts5_available:
                        self._rebuild_fts(conn)
                    conn.execute("PRAGMA user_version = 2")
                    current = 2
                if current < 3:
                    _add_column_if_missing(
                        conn,
                        "agent_runs",
                        "pending_compaction_json",
                        "TEXT",
                    )
                    conn.execute("PRAGMA user_version = 3")
                    current = 3
                if current < 4:
                    _add_column_if_missing(
                        conn,
                        "agent_runs",
                        "runtime_engine",
                        "TEXT NOT NULL DEFAULT 'langgraph-v1'",
                    )
                    _add_column_if_missing(
                        conn,
                        "agent_runs",
                        "runtime_state_version",
                        "INTEGER NOT NULL DEFAULT 0",
                    )
                    _add_column_if_missing(
                        conn,
                        "agent_runs",
                        "runtime_state_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    )
                    conn.executescript(_SCHEMA_V4)
                    conn.execute("PRAGMA user_version = 4")
                conn.commit()
            if self.path.exists():
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            self._initialized = True

    def _initialize_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(_FTS_V1)
        except sqlite3.OperationalError as exc:
            if "fts5" not in str(exc).casefold():
                raise
            self.fts5_available = False
        else:
            self.fts5_available = True

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM messages_fts")
        conn.executemany(
            "INSERT INTO messages_fts(message_id, session_id, content) VALUES (?, ?, ?)",
            [
                (row[0], row[1], fts_index_text(str(row[2])))
                for row in conn.execute("SELECT id, session_id, content FROM messages")
            ],
        )
        conn.execute("DELETE FROM project_memories_fts")
        conn.executemany(
            "INSERT INTO project_memories_fts(memory_id, title, kind, content) VALUES (?, ?, ?, ?)",
            [
                (
                    row[0],
                    fts_index_text(str(row[1])),
                    fts_index_text(str(row[2])),
                    fts_index_text(str(row[3])),
                )
                for row in conn.execute(
                    "SELECT id, title, kind, content FROM project_memories"
                )
            ],
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


_SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    title_source TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    workspace_id TEXT,
    provider TEXT,
    model TEXT,
    thinking_level TEXT,
    composer_mode TEXT NOT NULL DEFAULT 'chat'
);
CREATE INDEX IF NOT EXISTS idx_local_sessions_user_updated
    ON sessions(user_id, archived_at, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_local_messages_session_created
    ON messages(session_id, created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_local_messages_run_role
    ON messages(session_id, source_run_id, role)
    WHERE source_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_summaries (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    summarized_message_count INTEGER NOT NULL,
    through_message_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_chars INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    default_provider TEXT,
    default_model TEXT,
    default_thinking_level TEXT,
    default_workspace_id TEXT,
    default_composer_mode TEXT NOT NULL DEFAULT 'chat',
    last_active_session_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage_records (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    workspace_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    thoughts_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    operation TEXT NOT NULL,
    resource_id TEXT,
    requested_provider TEXT,
    requested_model TEXT,
    input_count_method TEXT NOT NULL,
    budget_decision TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_usage_session ON token_usage_records(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_local_usage_workspace ON token_usage_records(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    status TEXT NOT NULL,
    checkpoint_id TEXT,
    latest_node TEXT,
    next_nodes_json TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    pending_approval_json TEXT,
    errors_json TEXT NOT NULL,
    control_action TEXT,
    steering_messages_json TEXT NOT NULL,
    pending_compaction_json TEXT,
    run_context_snapshot_json TEXT,
    runtime_engine TEXT NOT NULL DEFAULT 'langgraph-v1',
    runtime_state_version INTEGER NOT NULL DEFAULT 0,
    runtime_state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_runs_conversation_created
    ON agent_runs(conversation_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS agent_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    node TEXT,
    summary TEXT NOT NULL,
    output_json TEXT NOT NULL,
    UNIQUE(run_id, event_key)
);

CREATE TABLE IF NOT EXISTS agent_tool_executions (
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    call_id TEXT NOT NULL,
    name TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    response_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, call_id)
);

CREATE TABLE IF NOT EXISTS agent_runtime_snapshots (
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    boundary TEXT NOT NULL,
    runtime_engine TEXT NOT NULL,
    runtime_state_version INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, snapshot_id),
    UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspace_memory_settings (
    workspace_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_memories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    workspace_revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    importance INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    supersedes_id TEXT,
    expires_at TEXT,
    last_confirmed_at TEXT,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    conflict INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_project_memories_scope
    ON project_memories(workspace_id, workspace_revision, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_project_memories_key
    ON project_memories(workspace_id, workspace_revision, canonical_key);

CREATE TABLE IF NOT EXISTS project_memory_evidence (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES project_memories(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    content_hash TEXT,
    excerpt TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_extraction_jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    workspace_revision INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    active_count INTEGER NOT NULL,
    error TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(workspace_id, source_type, source_id)
);

CREATE TABLE IF NOT EXISTS memory_index_outbox (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    memory_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_memory_outbox_status
    ON memory_index_outbox(status, created_at);

CREATE TABLE IF NOT EXISTS memory_audit_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_memory_vectors (
    memory_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    workspace_revision INTEGER NOT NULL,
    memory_version INTEGER NOT NULL,
    dimensions INTEGER NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_memory_vectors_scope
    ON project_memory_vectors(workspace_id, workspace_revision);

CREATE TABLE IF NOT EXISTS user_memory_settings (
    user_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    importance INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    supersedes_id TEXT,
    last_confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_user_memories_scope
    ON user_memories(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_user_memories_key
    ON user_memories(user_id, canonical_key);

CREATE TABLE IF NOT EXISTS user_memory_evidence (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES user_memories(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    excerpt TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile_snapshots (
    user_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    source_memory_ids_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


_FTS_V1 = r"""
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id UNINDEXED,
    session_id UNINDEXED,
    content,
    tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS project_memories_fts USING fts5(
    memory_id UNINDEXED,
    title,
    kind,
    content,
    tokenize='unicode61'
);
"""


_SCHEMA_V2 = r"""
CREATE TABLE IF NOT EXISTS user_memory_scenes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_memory_ids_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_local_user_memory_scenes_scope
    ON user_memory_scenes(user_id, updated_at DESC, id DESC);
"""


_SCHEMA_V4 = r"""
CREATE TABLE IF NOT EXISTS agent_runtime_snapshots (
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    boundary TEXT NOT NULL,
    runtime_engine TEXT NOT NULL,
    runtime_state_version INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, snapshot_id),
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_local_runtime_snapshots_run_sequence
    ON agent_runtime_snapshots(run_id, sequence DESC);
"""


__all__ = ["LocalStateDatabase", "SCHEMA_VERSION"]
