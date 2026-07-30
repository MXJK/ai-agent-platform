from datetime import datetime, timezone
import unittest

from ai_agent_platform.project_memory.models import ProjectMemory
from ai_agent_platform.project_memory.vector import QdrantMemoryVectorStore


def memory(
    memory_id: str,
    *,
    workspace_id: str,
    revision: int,
    version: int,
) -> ProjectMemory:
    now = datetime.now(timezone.utc)
    return ProjectMemory(
        id=memory_id,
        workspace_id=workspace_id,
        workspace_revision=revision,
        kind="decision",
        title=f"Memory {memory_id}",
        content=f"Content {memory_id}",
        canonical_key=f"decision:{memory_id}",
        status="active",
        confidence=1.0,
        importance=3,
        version=version,
        created_by="tester",
        created_at=now,
        updated_at=now,
    )


class QdrantProjectMemoryTests(unittest.TestCase):
    def test_filters_workspace_revision_updates_versions_and_hard_deletes(self) -> None:
        store = QdrantMemoryVectorStore(
            url=":memory:",
            api_key=None,
            collection_name="project_memory_contract",
        )
        alpha_v1 = memory(
            "mem_0000000000000001",
            workspace_id="alpha",
            revision=1,
            version=1,
        )
        alpha_v2 = memory(
            "mem_0000000000000002",
            workspace_id="alpha",
            revision=2,
            version=1,
        )
        beta = memory(
            "mem_0000000000000003",
            workspace_id="beta",
            revision=1,
            version=1,
        )
        store.upsert(alpha_v1, [1.0, 0.0, 0.0])
        store.upsert(alpha_v2, [1.0, 0.0, 0.0])
        store.upsert(beta, [1.0, 0.0, 0.0])

        self.assertEqual(
            [item[0] for item in store.search(
                workspace_id="alpha",
                workspace_revision=1,
                query_embedding=[1.0, 0.0, 0.0],
                limit=10,
            )],
            [alpha_v1.id],
        )
        self.assertEqual(
            store.list_indexed(workspace_id="alpha"),
            {
                alpha_v1.id: (1, 1),
                alpha_v2.id: (2, 1),
            },
        )

        updated = ProjectMemory(
            **{
                **alpha_v1.__dict__,
                "version": 2,
                "content": "Updated content",
            }
        )
        store.upsert(updated, [0.0, 1.0, 0.0])
        self.assertEqual(
            store.list_indexed(workspace_id="alpha")[alpha_v1.id],
            (1, 2),
        )
        store.delete(alpha_v1.id)
        self.assertNotIn(
            alpha_v1.id,
            store.list_indexed(workspace_id="alpha"),
        )
        self.assertEqual(
            store.search(
                workspace_id="alpha",
                workspace_revision=1,
                query_embedding=[0.0, 1.0, 0.0],
                limit=10,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
