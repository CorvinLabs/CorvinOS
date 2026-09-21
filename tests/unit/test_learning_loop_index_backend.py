"""The learning-loop index must be constructible on a stock install.

Two independent defects made the /app/learning-loops panel answer HTTP 503
"Learning loop service not available" on every install, and both were invisible
because the console route catches the failure and degrades:

1. ``core/knowledge_graph/__init__.py`` re-exported a ``.mcp_tools`` module that
   was never written, so importing ANY submodule of the package raised
   ModuleNotFoundError before a single line of loop code ran.
2. ``LearningLoopIndexStorage`` required plyvel (a C binding over libleveldb).
   plyvel appears in no requirements file in this repo, libleveldb is not
   installed, and neither is available on the Windows releases — so the
   constructor raised ImportError unconditionally.

These tests fail if either regresses. Runnable with pytest or
``python3 -m unittest tests.unit.test_learning_loop_index_backend``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestKnowledgeGraphPackageImports(unittest.TestCase):
    def test_package_and_submodules_import_in_a_fresh_interpreter(self) -> None:
        """A fresh interpreter must reach the service through the package."""
        code = (
            "import core.knowledge_graph\n"
            "from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService\n"
            "from core.knowledge_graph.storage.learning_loop_index import LearningLoopIndexStorage\n"
            "print('ok')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_package_exports_nothing_that_does_not_exist(self) -> None:
        """Every name in __all__ must actually be importable from the package."""
        import core.knowledge_graph as kg

        for name in getattr(kg, "__all__", []):
            self.assertTrue(hasattr(kg, name), f"__all__ names missing attribute {name!r}")


class TestIndexStorageWithoutLevelDB(unittest.TestCase):
    def test_storage_opens_and_round_trips_without_plyvel(self) -> None:
        from core.knowledge_graph.storage.learning_loop_index import (
            LearningLoopIndexEntry,
            LearningLoopIndexStorage,
        )

        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            storage = LearningLoopIndexStorage("_default", Path(tmp) / "index")
            try:
                entry = LearningLoopIndexEntry(
                    tenant_id="_default",
                    plugin_id="plugin:test",
                    loop_id="loop-1",
                    description="round trip",
                    event_source="SkillExecutedEvent.confidence_score",
                    feedback_types=["outcome"],
                    aggregation="rolling_mean_7d",
                    health_threshold=0.5,
                    dormancy_alert_hours=24,
                    owner_skill=None,
                    last_event_ts=now,
                    event_count_7d=3,
                    health_score=0.75,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                storage.insert(entry)

                fetched = storage.get("plugin:test", "loop-1")
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.health_score, 0.75)
                self.assertEqual(fetched.event_count_7d, 3)

                listed = storage.list_all()
                self.assertEqual([e.loop_id for e in listed], ["loop-1"])

                self.assertTrue(storage.delete("plugin:test", "loop-1"))
                self.assertEqual(storage.list_all(), [])
            finally:
                storage.close()

    def test_list_all_is_scoped_to_its_own_tenant(self) -> None:
        """A prefix scan must not return another tenant's rows (GDPR Art. 5)."""
        from core.knowledge_graph.storage.learning_loop_index import (
            LearningLoopIndexEntry,
            LearningLoopIndexStorage,
        )

        now = datetime.now(timezone.utc)

        def make(tenant: str) -> LearningLoopIndexEntry:
            return LearningLoopIndexEntry(
                tenant_id=tenant,
                plugin_id="plugin:test",
                loop_id=f"loop-{tenant}",
                description="",
                event_source="",
                feedback_types=[],
                aggregation="rolling_mean_7d",
                health_threshold=None,
                dormancy_alert_hours=24,
                owner_skill=None,
                last_event_ts=now,
                event_count_7d=0,
                health_score=0.5,
                status="active",
                created_at=now,
                updated_at=now,
            )

        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "index"
            a = LearningLoopIndexStorage("_default", shared)
            b = LearningLoopIndexStorage("other", shared)
            try:
                a.insert(make("_default"))
                b.insert(make("other"))
                self.assertEqual([e.loop_id for e in a.list_all()], ["loop-_default"])
                self.assertEqual([e.loop_id for e in b.list_all()], ["loop-other"])
            finally:
                a.close()
                b.close()


if __name__ == "__main__":
    unittest.main()
