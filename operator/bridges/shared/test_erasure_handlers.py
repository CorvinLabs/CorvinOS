"""Tests for the per-layer ErasureHandler implementations."""
from __future__ import annotations

import json
import os
import shutil
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
    WebChatHandler,
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


# ── L7 + L24 (real purges since R4-F4) + the identity-mapping base ──────


class TestStubHandlers(unittest.TestCase):
    """R4-F4 rewrite: these two asserted the handlers returned the STUB TEXT
    ("not yet implemented"), which is what ``COVERED_DIRS`` was simultaneously
    claiming was covered — a subject-named file in ``skill-forge``/``skills``/
    ``global/data`` survived a ``completed`` erasure. The handlers now purge for
    real, so the assertions pin the purge instead of the excuse."""

    def _home(self):
        import os
        import tempfile
        tmp = tempfile.mkdtemp(prefix="er-l7l24-")
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("CORVIN_HOME", old)
                        if old is not None else os.environ.pop("CORVIN_HOME", None))
        return Path(tmp) / "tenants" / "_default"

    def test_l7_erases_a_subject_named_skill_and_keeps_others(self):
        t = self._home()
        (t / "skill-forge" / "skills").mkdir(parents=True)
        (t / "skills").mkdir(parents=True)
        (t / "skill-forge" / "user_42.json").write_text(json.dumps({"user_id": "user_42"}))
        (t / "skills" / "s.json").write_text(json.dumps({"created_by": "user_42"}))
        (t / "skills" / "keep.json").write_text(json.dumps({"created_by": "user_99"}))

        r = L7SkillForgeHandler(tenant_id="_default").purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.APPLIED, r.reason)
        self.assertEqual(r.layer_id, "L7-skill-forge")
        self.assertFalse((t / "skill-forge" / "user_42.json").exists())
        self.assertFalse((t / "skills" / "s.json").exists())
        self.assertTrue((t / "skills" / "keep.json").exists())

    def test_l7_reports_store_absent_when_nothing_is_installed(self):
        self._home()
        r = L7SkillForgeHandler(tenant_id="_default").purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertEqual(r.code, "store_absent")

    def test_l24_erases_a_snapshot_manifest_naming_the_subject(self):
        t = self._home()
        (t / "global" / "data").mkdir(parents=True)
        (t / "global" / "data" / "snap.json").write_text(json.dumps({"chat_key": "user_42"}))
        (t / "global" / "data" / "keep.json").write_text(json.dumps({"chat_key": "user_99"}))

        r = L24DataSnapshotHandler(tenant_id="_default").purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.APPLIED, r.reason)
        self.assertEqual(r.layer_id, "L24-data-snapshot")
        self.assertFalse((t / "global" / "data" / "snap.json").exists())
        self.assertTrue((t / "global" / "data" / "keep.json").exists())

    def test_l24_reports_store_absent_when_no_snapshots_exist(self):
        self._home()
        r = L24DataSnapshotHandler(tenant_id="_default").purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertEqual(r.code, "store_absent")

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


class TestWebChatHandler(unittest.TestCase):
    """ADR-0194 voice archive + turn log must actually be erased on Art. 17.

    Before WebChatHandler existed, an erasure run reported APPLIED across every
    layer and wrote a successful receipt into the hash-chained audit log while
    <tenant>/sessions/web:<sid>/voice/*.ogg — a spoken rendering of every
    assistant reply in the chat — survived untouched: voice/ is a SIBLING of
    artifacts/, so L33ArtifactHandler never saw it.
    """

    def _corvin_home(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(prefix="erasure-webchat-")

    def _seed(self, td: str, session_key: str = "web:abc123"):
        sid = session_key.split(":", 1)[1]
        vdir = Path(td) / "tenants" / "_default" / "sessions" / session_key / "voice"
        vdir.mkdir(parents=True)
        (vdir / "deadbeefdeadbeef.ogg").write_bytes(b"OggS" + b"\0" * 96)
        (vdir / "deadbeefdeadbeef-f00.ogg").write_bytes(b"OggS" + b"\0" * 96)
        turns = Path(td) / "tenants" / "_default" / "global" / "web_chat" / "sessions"
        turns.mkdir(parents=True)
        (turns / f"{sid}.turns.jsonl").write_text('{"role":"assistant"}\n')
        return vdir, turns / f"{sid}.turns.jsonl"

    def test_purge_removes_voice_archive_and_turn_log(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                vdir, turns = self._seed(td)
                result = WebChatHandler().purge("web:abc123", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 3)  # 2 audio + 1 turn log
                self.assertEqual(list(vdir.glob("*")), [])
                self.assertFalse(turns.exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_removes_session_meta_file_with_title(self):
        """GDPR: `<sid>.json` (chat_runtime._meta_path) carries `title`,
        derived from the user's FIRST chat message — verbatim user content.
        The first cut of the handler purged the turn log but left this meta
        file sitting right next to it, so an Art. 17 run wrote an APPLIED
        receipt while the opening question survived on disk (found
        2026-07-17). `.json.tmp` is _write_meta's crash-orphaned staging file
        and must be swept too."""
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                _, turns = self._seed(td)
                store = turns.parent
                meta = store / "abc123.json"
                meta.write_text('{"title": "Wie lese ich meinen Befund vom 3.7.?"}')
                meta_tmp = store / "abc123.json.tmp"
                meta_tmp.write_text('{"title": "Wie lese ich"}')
                result = WebChatHandler().purge("web:abc123", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertFalse(meta.exists(), "session meta (PII title) survived erasure")
                self.assertFalse(meta_tmp.exists(), "stale meta .tmp survived erasure")
                self.assertEqual(result.count, 5)  # 2 audio + 1 turn log + meta + meta.tmp
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_removes_attachments_and_compute_inbox(self):
        """Same gap class, next siblings (adversarial round, 2026-07-17):
        <workdir>/attachments/ holds RAW user uploads (routes/chat.py
        upload_attachments) and <workdir>/compute_inbox/*_result.json
        carries the user's task text in `description` (plus the processed/
        mirror). Neither was reachable by any handler — an Art. 17 receipt
        said APPLIED while the uploads survived."""
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                workdir = Path(td) / "tenants" / "_default" / "sessions" / "web:abc123"
                attach = workdir / "attachments"
                attach.mkdir(parents=True)
                (attach / "befund-scan.pdf").write_bytes(b"%PDF-1.4 patient data")
                inbox = workdir / "compute_inbox"
                (inbox / "processed").mkdir(parents=True)
                (inbox / "t1_result.json").write_text(
                    '{"description": "Analysiere meinen Befund vom 3.7."}')
                (inbox / "processed" / "t0_result.json").write_text(
                    '{"description": "alte Task-Beschreibung"}')
                result = WebChatHandler().purge("web:abc123", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 3)
                self.assertFalse(attach.exists(), "attachments/ survived erasure")
                self.assertFalse(inbox.exists(), "compute_inbox/ survived erasure")
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_purge_removes_cel_briefs(self):
        """ADR-0278 Layer B: <workdir>/cel-briefs/<hash>.txt is the FULL rendered
        context brief text (user task + memory passages) — PII by construction. It
        is a sibling of voice/ that no other handler owns, so without this it would
        be the exact orphaning bug this handler was written for: an Art. 17 run
        reporting APPLIED while the brief text survived on disk."""
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                workdir = Path(td) / "tenants" / "_default" / "sessions" / "web:abc123"
                briefs = workdir / "cel-briefs"
                briefs.mkdir(parents=True)
                (briefs / "9f2bdeadbeef.txt").write_text(
                    "## Context brief\nRelevant past memory: patient Befund vom 3.7.")
                result = WebChatHandler().purge("web:abc123", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertFalse(briefs.exists(), "cel-briefs/ survived erasure")
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_absent_session_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                result = WebChatHandler().purge("web:nothing-here", "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertEqual(result.count, 0)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_windows_sanitised_dir_name_is_also_purged(self):
        """The console rewrites 'web:<sid>' to 'web_<sid>' on Windows."""
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                vdir = (Path(td) / "tenants" / "_default" / "sessions"
                        / "web_abc123" / "voice")
                vdir.mkdir(parents=True)
                (vdir / "cafebabecafebabe.ogg").write_bytes(b"OggS")
                result = WebChatHandler().purge("web:abc123", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(list(vdir.glob("*")), [])
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_handler_is_registered_in_the_real_chain(self):
        """A handler nobody registers erases nothing — that WAS the bug."""
        ids = {getattr(h, "layer_id", None) for h in real_handler_chain()}
        self.assertIn("web-chat", ids)


# ── R3 follow-ups: filename attribution + crash-atomic rewrites ─────────────


class TestFilenameIsAttribution(unittest.TestCase):
    """A subject id that appears ONLY in a filename.

    Erasure attributed a path two ways: a DIRECTORY named after the subject, or
    a JSON/JSONL PAYLOAD naming it under a known identity key. A file whose only
    mention of the subject is its own name — ``<subject>.json``,
    ``snapshot_<subject>.jsonl`` — matched neither and survived a run the
    orchestrator then reported COMPLETED. A filename is personal data.
    """

    def test_a_file_named_after_the_subject_is_erased(self):
        from erasure_handlers import VibeCheckpointHandler

        with tempfile.TemporaryDirectory(prefix="erasure-fname-") as td:
            os.environ["CORVIN_HOME"] = td
            try:
                root = Path(td) / "tenants" / "_default" / "vibe" / "checkpoints"
                root.mkdir(parents=True)
                # Payload never names the subject — only the filename does.
                (root / "subj-42.json").write_text('{"state": "opaque"}')
                (root / "snapshot_subj-42.jsonl").write_text('{"a":1}\n')
                # A DIFFERENT subject whose id merely CONTAINS ours must survive.
                (root / "subj-421.json").write_text('{"state": "other"}')
                (root / "unrelated.json").write_text('{"state": "keep"}')

                result = VibeCheckpointHandler().purge("subj-42", "er-test")
                self.assertEqual(result.status, LayerStatus.APPLIED, result.reason)
                self.assertFalse((root / "subj-42.json").exists())
                self.assertFalse((root / "snapshot_subj-42.jsonl").exists())
                self.assertTrue((root / "subj-421.json").exists())
                self.assertTrue((root / "unrelated.json").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_token_boundaries_are_respected(self):
        from erasure_handlers import _name_names_subject

        self.assertTrue(_name_names_subject("u1", "u1"))
        self.assertTrue(_name_names_subject("u1.json", "u1"))
        self.assertTrue(_name_names_subject("snapshot_u1.jsonl", "u1"))
        self.assertTrue(_name_names_subject("u1-profile.json", "u1"))
        # Over-matching deletes ANOTHER subject's data — its own Art. 5 breach.
        self.assertFalse(_name_names_subject("u12.json", "u1"))
        self.assertFalse(_name_names_subject("xu1.json", "u1"))
        self.assertFalse(_name_names_subject("u1x.json", "u1"))
        self.assertFalse(_name_names_subject("anything.json", ""))


class TestErasureRewritesAreCrashAtomic(unittest.TestCase):
    """Every entry-wise rewrite was ``tmp.write_text`` + ``os.replace``: the
    rename is atomic, the DATA was never flushed, and the staging name was fixed
    so two concurrent erasures clobbered each other."""

    def test_the_index_rewrite_flushes_and_uses_a_unique_staging_name(self):
        import erasure_handlers as EH

        with tempfile.TemporaryDirectory(prefix="erasure-atomic-") as td:
            target = Path(td) / "index.json"
            target.write_text('["old"]')
            seen = {}
            real_fsync = os.fsync

            def _watch(fd):
                seen["fsync"] = seen.get("fsync", 0) + 1
                return real_fsync(fd)

            os.fsync = _watch
            try:
                EH._atomic_replace_text(target, '["new"]')
            finally:
                os.fsync = real_fsync
            self.assertEqual(target.read_text(), '["new"]')
            # file + directory
            self.assertGreaterEqual(seen.get("fsync", 0), 1)
            self.assertEqual(sorted(x.name for x in Path(td).iterdir()), ["index.json"])

    def test_a_failed_write_leaves_the_original_and_no_staging_file(self):
        import erasure_handlers as EH

        with tempfile.TemporaryDirectory(prefix="erasure-atomic2-") as td:
            target = Path(td) / "index.json"
            target.write_text('["old"]')

            real_replace = os.replace

            def _boom(src, dst):
                raise OSError("disk full")

            os.replace = _boom
            try:
                with self.assertRaises(OSError):
                    EH._atomic_replace_text(target, '["new"]')
            finally:
                os.replace = real_replace
            self.assertEqual(target.read_text(), '["old"]')
            self.assertEqual(sorted(x.name for x in Path(td).iterdir()), ["index.json"])
