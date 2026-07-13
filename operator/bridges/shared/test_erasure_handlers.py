"""Tests for the per-layer ErasureHandler implementations."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from erasure_handlers import (  # noqa: E402
    IdentityMappingHandlerBase,
    L7SkillForgeHandler,
    L24DataSnapshotHandler,
    L28RecallHandler,
    L33ArtifactHandler,
    WorkflowCheckpointHandler,
    real_handler_chain,
)
from erasure_orchestrator import LayerStatus  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS_SRC = _REPO_ROOT / "core" / "workflows"
if str(_WORKFLOWS_SRC) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS_SRC))


# ── L28 ──────────────────────────────────────────────────────────────


class TestL28RecallHandler(unittest.TestCase):

    def _build_db(self, path: Path, rows: list[tuple[str, str]]) -> None:
        """rows: [(chat_key, user_text), ...] — simplified turns table."""
        with sqlite3.connect(str(path)) as conn:
            conn.execute("""
                CREATE TABLE turns (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL NOT NULL,
                    channel   TEXT NOT NULL,
                    chat_key  TEXT NOT NULL,
                    msg_id    TEXT,
                    run_id    TEXT,
                    persona   TEXT,
                    user_chars INTEGER NOT NULL,
                    asst_chars INTEGER NOT NULL,
                    redacted_classes TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    asst_text TEXT NOT NULL
                )
            """)
            for i, (chat_key, text) in enumerate(rows):
                conn.execute(
                    "INSERT INTO turns(ts, channel, chat_key, msg_id, run_id, persona, "
                    "user_chars, asst_chars, redacted_classes, user_text, asst_text) "
                    "VALUES(?, 'discord', ?, ?, '', '', ?, 0, '[]', ?, '')",
                    (1.0 + i, chat_key, f"m{i}", len(text), text),
                )
            conn.commit()

    def test_purge_deletes_matching_rows(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "recall.db"
            self._build_db(db, [
                ("user_42", "hello"),
                ("user_42", "world"),
                ("user_99", "other"),
            ])
            handler = L28RecallHandler(db_path=db)
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.APPLIED)
            self.assertEqual(result.count, 2)
            # Verify the other user's rows are still there
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
            self.assertEqual(row[0], 1)

    def test_purge_no_match_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "recall.db"
            self._build_db(db, [("user_99", "x")])
            handler = L28RecallHandler(db_path=db)
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.SKIPPED)
            self.assertEqual(result.count, 0)

    def test_missing_db_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            handler = L28RecallHandler(
                db_path=Path(td) / "nonexistent.db",
            )
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.SKIPPED)
            self.assertIn("not present", result.reason)

    def test_layer_id_default(self):
        h = L28RecallHandler()
        self.assertEqual(h.layer_id, "L28-recall")


# ── L33 ──────────────────────────────────────────────────────────────


class TestL33ArtifactHandler(unittest.TestCase):

    def _corvin_home(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(prefix="erasure-l33-")

    def test_purge_removes_session_files(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                session_key = "discord:user_42"
                art_dir = (Path(td) / "tenants" / "_default" / "sessions"
                           / session_key / "artifacts")
                art_dir.mkdir(parents=True)
                # Two files + a manifest entry
                (art_dir / "report.pdf").write_bytes(b"x" * 100)
                (art_dir / ".manifest.jsonl").write_text(
                    '{"name":"report.pdf"}\n'
                )

                handler = L33ArtifactHandler()
                result = handler.purge(session_key, "er-test")

                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 2)
                self.assertFalse((art_dir / "report.pdf").exists())
                self.assertFalse((art_dir / ".manifest.jsonl").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_missing_session_dir_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                handler = L33ArtifactHandler()
                result = handler.purge("discord:never_existed",
                                       "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertIn("no session artifacts", result.reason)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_empty_artifacts_dir_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                session_key = "discord:user_42"
                art_dir = (Path(td) / "tenants" / "_default" / "sessions"
                           / session_key / "artifacts")
                art_dir.mkdir(parents=True)
                handler = L33ArtifactHandler()
                result = handler.purge(session_key, "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertIn("empty", result.reason)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_layer_id_default(self):
        h = L33ArtifactHandler()
        self.assertEqual(h.layer_id, "L33-artifacts")


# ── Workflow checkpoints (ADR-0188 M5) ────────────────────────────────


class TestWorkflowCheckpointHandler(unittest.TestCase):
    """GDPR Art. 17 coverage for paused Task-Engine workflow checkpoints.

    Uses the real ``corvin_workflows.checkpoint`` module to write the
    checkpoint (not a hand-rolled JSON fixture) so the test breaks if the
    on-disk schema the handler parses ever drifts from what the runner
    actually writes.
    """

    def _corvin_home(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(prefix="erasure-wf-")

    def test_purge_deletes_matching_checkpoint(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                import importlib
                from corvin_workflows import checkpoint as cp  # noqa: E402
                importlib.reload(cp)  # pick up the CORVIN_HOME just set

                cp.save(
                    "run-subject",
                    workflow_path="wf.yaml",
                    workflow_name="expense-approval",
                    inputs={"amount": 500, "requester_email": "alice@example.com"},
                    state={"step": "await_manager"},
                    completed_ids=["start"],
                    paused_at_node="ask_human",
                    prompt="Approve $500 expense?",
                    channel="discord",
                    chat_id="discord_user_42",
                    expect=None,
                )
                cp.save(
                    "run-other",
                    workflow_path="wf.yaml",
                    workflow_name="expense-approval",
                    inputs={"amount": 10},
                    state={},
                    completed_ids=[],
                    paused_at_node="ask_human",
                    prompt="Approve $10 expense?",
                    channel="discord",
                    chat_id="discord_user_99",
                    expect=None,
                )

                runs_dir = Path(td) / "tenants" / "_default" / "workflow_runs"
                self.assertTrue((runs_dir / "run-subject.json").exists())
                self.assertTrue((runs_dir / "run-other.json").exists())

                handler = WorkflowCheckpointHandler()
                result = handler.purge("discord_user_42", "er-test")

                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 1)
                # The subject's checkpoint (and its raw chat_id + PII-bearing
                # inputs) is gone ...
                self.assertFalse((runs_dir / "run-subject.json").exists())
                # ... but the other user's paused run is untouched.
                self.assertTrue((runs_dir / "run-other.json").exists())
                still_there = json.loads(
                    (runs_dir / "run-other.json").read_text(encoding="utf-8")
                )
                self.assertEqual(still_there["chat_id"], "discord_user_99")
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_matches_on_approver_when_not_chat_id(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                import importlib
                from corvin_workflows import checkpoint as cp  # noqa: E402
                importlib.reload(cp)

                # Approver differs from the chat_id that paused the run —
                # e.g. a manager approving on behalf of a requester's ticket.
                cp.save(
                    "run-approver",
                    workflow_path="wf.yaml",
                    workflow_name="it-ticket",
                    inputs={},
                    state={},
                    completed_ids=[],
                    paused_at_node="ask_human",
                    prompt="Approve ticket?",
                    channel="discord",
                    chat_id="discord_requester_1",
                    expect=None,
                    approver="discord_manager_7",
                )

                handler = WorkflowCheckpointHandler()
                result = handler.purge("discord_manager_7", "er-test")

                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 1)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_no_match_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                import importlib
                from corvin_workflows import checkpoint as cp  # noqa: E402
                importlib.reload(cp)

                cp.save(
                    "run-untouched",
                    workflow_path="wf.yaml",
                    workflow_name="expense-approval",
                    inputs={},
                    state={},
                    completed_ids=[],
                    paused_at_node="ask_human",
                    prompt="Approve?",
                    channel="discord",
                    chat_id="discord_user_99",
                    expect=None,
                )

                handler = WorkflowCheckpointHandler()
                result = handler.purge("discord_user_42", "er-test")

                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertEqual(result.count, 0)
                # Nothing was deleted for the non-matching subject.
                runs_dir = Path(td) / "tenants" / "_default" / "workflow_runs"
                self.assertTrue((runs_dir / "run-untouched.json").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_missing_runs_dir_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                handler = WorkflowCheckpointHandler()
                result = handler.purge("discord_user_42", "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertIn("no workflow_runs dir", result.reason)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_also_deletes_claimed_sidecar(self):
        """A resume in flight parks the checkpoint under `.json.claimed`
        (checkpoint.claim()) — erasure must still find and remove it."""
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                import importlib
                from corvin_workflows import checkpoint as cp  # noqa: E402
                importlib.reload(cp)

                cp.save(
                    "run-claimed",
                    workflow_path="wf.yaml",
                    workflow_name="expense-approval",
                    inputs={},
                    state={},
                    completed_ids=[],
                    paused_at_node="ask_human",
                    prompt="Approve?",
                    channel="discord",
                    chat_id="discord_user_42",
                    expect=None,
                )
                cp.claim("run-claimed")

                runs_dir = Path(td) / "tenants" / "_default" / "workflow_runs"
                self.assertFalse((runs_dir / "run-claimed.json").exists())
                self.assertTrue((runs_dir / "run-claimed.json.claimed").exists())

                handler = WorkflowCheckpointHandler()
                result = handler.purge("discord_user_42", "er-test")

                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 1)
                self.assertFalse((runs_dir / "run-claimed.json.claimed").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_layer_id_default(self):
        h = WorkflowCheckpointHandler()
        self.assertEqual(h.layer_id, "L-workflow-checkpoints")


# ── L7 + L24 stubs ───────────────────────────────────────────────────


class TestStubHandlers(unittest.TestCase):

    def test_l7_returns_skipped_with_documented_reason(self):
        h = L7SkillForgeHandler()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("not yet implemented", r.reason)
        self.assertEqual(r.layer_id, "L7-skill-forge")

    def test_l24_returns_skipped_with_documented_reason(self):
        h = L24DataSnapshotHandler()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("not yet implemented", r.reason)
        self.assertEqual(r.layer_id, "L24-data-snapshot")

    def test_identity_mapping_base_warns_when_unconfigured(self):
        h = IdentityMappingHandlerBase()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("no concrete", r.reason)

    def test_identity_mapping_subclassable(self):
        from dataclasses import dataclass
        from erasure_orchestrator import ErasureLayerResult, LayerStatus as LS

        @dataclass
        class _RealMapping(IdentityMappingHandlerBase):
            def purge(self, subject_id, request_id):
                return ErasureLayerResult(
                    layer_id=self.layer_id,
                    status=LS.APPLIED,
                    count=1,
                    reason="mapping deleted",
                )

        r = _RealMapping().purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.APPLIED)


# ── default chain factory ────────────────────────────────────────────


class TestRealHandlerChain(unittest.TestCase):

    def test_chain_includes_core_handlers(self):
        chain = real_handler_chain()
        layer_ids = [h.layer_id for h in chain]
        # Core per-layer handlers that must always be present. ACS-traces
        # was added (ADR-0127 review) to purge plaintext WDAT worker traces
        # under GDPR Art. 17 — assert it is wired into the default chain.
        for required in ("L28-recall", "L33-artifacts", "ACS-traces",
                         "L-workflow-checkpoints",
                         "L7-skill-forge", "L24-data-snapshot"):
            self.assertIn(required, layer_ids, f"missing handler: {required}")
        # No duplicate layer ids in the default chain.
        self.assertEqual(len(layer_ids), len(set(layer_ids)))


if __name__ == "__main__":
    unittest.main()
