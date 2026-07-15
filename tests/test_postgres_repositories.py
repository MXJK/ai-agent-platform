import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ai_agent_platform.repositories.postgres import (
    PostgresAgentRunRepository,
    PostgresDocumentRepository,
    PostgresRepositoryIndexRepository,
    PostgresSessionRepository,
)


class PostgresRepositoryTests(unittest.TestCase):
    def test_constructors_do_not_initialize_schema(self) -> None:
        class PsycopgSentinel:
            def connect(self, database_url):
                raise AssertionError("constructors should not connect to PostgreSQL")

        database_url = "postgresql://tester:secret@localhost:5432/test_agent_platform"
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=PsycopgSentinel(),
        ) as require_psycopg:
            session_repository = PostgresSessionRepository(database_url=database_url)
            run_repository = PostgresAgentRunRepository(database_url=database_url)
            document_repository = PostgresDocumentRepository(database_url=database_url)
            index_repository = PostgresRepositoryIndexRepository(
                database_url=database_url
            )

        self.assertEqual(session_repository._database_url, database_url)
        self.assertEqual(run_repository._database_url, database_url)
        self.assertEqual(document_repository._database_url, database_url)
        self.assertEqual(index_repository._database_url, database_url)
        self.assertEqual(require_psycopg.call_count, 4)

    def test_repository_index_job_lifecycle_maps_rows(self) -> None:
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        job_row = (
            "idxjob_123",
            "repo_main",
            "/workspace/repo",
            ["**/*.py"],
            [".git/**"],
            1024,
            "pending",
            0,
            0,
            0,
            0,
            None,
            now,
            now,
            None,
        )
        completed_row = (
            "idxjob_123",
            "repo_main",
            "/workspace/repo",
            ["**/*.py"],
            [".git/**"],
            1024,
            "completed",
            3,
            2,
            1,
            0,
            None,
            now,
            now,
            now,
        )
        connection = FakeConnection([None, job_row, completed_row, None])

        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ), patch(
            "ai_agent_platform.repositories.postgres._require_jsonb",
            return_value=lambda value: value,
        ):
            repository = PostgresRepositoryIndexRepository(
                database_url="postgresql://tester:secret@localhost:5432/test"
            )
            repository._connect = lambda: connection
            job = repository.create_index_job(
                repository_id="repo_main",
                root_path="/workspace/repo",
                include_patterns=["**/*.py"],
                exclude_patterns=[".git/**"],
                max_file_size=1024,
            )
            completed_job = repository.update_index_job(
                job_id="idxjob_123",
                status="completed",
                scanned_files=3,
                indexed_files=2,
                skipped_files=1,
                failed_files=0,
            )

        self.assertEqual(job.id, "idxjob_123")
        self.assertEqual(job.repository_id, "repo_main")
        self.assertEqual(job.include_patterns, ["**/*.py"])
        self.assertEqual(job.status, "pending")
        self.assertEqual(completed_job.status, "completed")
        self.assertEqual(completed_job.scanned_files, 3)
        self.assertEqual(completed_job.completed_at, now)
        self.assertEqual(len(connection.calls), 4)
        self.assertEqual(connection.calls[-1][1], (now, "repo_main"))

    def test_repository_file_upsert_maps_metadata(self) -> None:
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        file_row = (
            "repofile_abc",
            "repo_main",
            "ai_agent_platform/main.py",
            "sha256",
            2048,
            "doc_123",
            now,
            None,
            now,
            now,
        )
        connection = FakeConnection([file_row])

        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresRepositoryIndexRepository(
                database_url="postgresql://tester:secret@localhost:5432/test"
            )
            repository._connect = lambda: connection
            record = repository.upsert_file(
                repository_id="repo_main",
                path="ai_agent_platform/main.py",
                content_hash="sha256",
                size_bytes=2048,
                document_id="doc_123",
                indexed_at=now,
            )

        self.assertEqual(record.repository_id, "repo_main")
        self.assertEqual(record.path, "ai_agent_platform/main.py")
        self.assertEqual(record.content_hash, "sha256")
        self.assertEqual(record.document_id, "doc_123")
        self.assertEqual(record.indexed_at, now)


class FakeCursor:
    def __init__(self, result):
        self._result = result

    def fetchone(self):
        return self._result

    def fetchall(self):
        return list(self._result)


class FakeConnection:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        result = self._results.pop(0) if self._results else None
        return FakeCursor(result)


if __name__ == "__main__":
    unittest.main()
