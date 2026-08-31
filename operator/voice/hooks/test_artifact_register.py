"""Unit tests for ``operator/voice/hooks/artifact_register.py``.

The hook itself is best-effort and always returns rc=0. These tests
exercise the predicates (path-convention, MIME-detect) and the
fork+detach path. The async description-generation and the
``index_turn`` call are mocked because they depend on the helper-model
CLI being on PATH.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "operator" / "forge"))
sys.path.insert(0, str(HERE.parents[2] / "operator" / "bridges" / "shared"))

import artifact_register as hook  # noqa: E402


# ── Sandbox ────────────────────────────────────────────────────────────────


class _Sandbox:
    """Sandboxed CORVIN_HOME + session-key + audit-event capture."""

    def __enter__(self) -> "_Sandbox":
        self.tmp = tempfile.TemporaryDirectory(prefix="corvin-hook-")
        self.root = Path(self.tmp.name)
        self.session_key = "test:hook-chat"
        self.env_patch = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root),
            "CORVIN_TENANT_ID": "_default",
            "CORVIN_SESSION_KEY": self.session_key,
            "VOICE_AUDIT_PATH": "",
        })
        self.env_patch.start()
        self.session_root = (self.root / "tenants" / "_default"
                             / "sessions" / self.session_key / "artifacts")
        self.session_root.mkdir(parents=True)
        self.events: list[tuple[str, str, dict]] = []
        # Patch `_emit` to capture events synchronously in the parent.
        self._emit_patch = mock.patch.object(
            hook, "_emit",
            side_effect=lambda et, *, severity, details:
                self.events.append((et, severity, details)))
        self._emit_patch.start()
        return self

    def __exit__(self, *exc) -> None:
        self._emit_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()


# ── Payload parsing ────────────────────────────────────────────────────────


class PayloadParsingTests(unittest.TestCase):
    def test_write_payload_extracts_file_path(self) -> None:
        p = hook._extract_output_path("Write",
                                      {"tool_input": {"file_path": "/tmp/x.pdf"}})
        self.assertEqual(p, Path("/tmp/x.pdf"))

    def test_edit_payload_extracts_file_path(self) -> None:
        p = hook._extract_output_path("Edit",
                                      {"tool_input": {"file_path": "/tmp/y.csv"}})
        self.assertEqual(p, Path("/tmp/y.csv"))

    def test_notebook_payload_prefers_notebook_path(self) -> None:
        p = hook._extract_output_path(
            "NotebookEdit",
            {"tool_input": {"notebook_path": "/tmp/n.ipynb",
                            "file_path": "/tmp/wrong"}})
        self.assertEqual(p, Path("/tmp/n.ipynb"))

    def test_unknown_tool_returns_none(self) -> None:
        p = hook._extract_output_path("Read", {"tool_input": {"file_path": "/x"}})
        self.assertIsNone(p)

    def test_missing_path_returns_none(self) -> None:
        p = hook._extract_output_path("Write", {"tool_input": {}})
        self.assertIsNone(p)


# ── MIME detection ─────────────────────────────────────────────────────────


class MimeDetectTests(unittest.TestCase):
    def test_pdf_magic(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(b"%PDF-1.4 content")
            path = Path(fh.name)
        try:
            self.assertEqual(hook._detect_mime(path), "application/pdf")
        finally:
            path.unlink()

    def test_png_magic(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(b"\x89PNG\r\n\x1a\nrest")
            path = Path(fh.name)
        try:
            self.assertEqual(hook._detect_mime(path), "image/png")
        finally:
            path.unlink()

    def test_text_falls_back_to_extension(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            fh.write(b"col1,col2\n1,2\n")
            path = Path(fh.name)
        try:
            self.assertEqual(hook._detect_mime(path), "text/csv")
        finally:
            path.unlink()


# ── Predicates ─────────────────────────────────────────────────────────────


class AutoRegisterPredicateTests(unittest.TestCase):
    def test_path_convention_triggers(self) -> None:
        with _Sandbox() as sbx:
            f = sbx.session_root / "report.txt"
            f.write_text("hi")
            ok, reason = hook._should_auto_register(f, sbx.session_root)
            self.assertTrue(ok)
            self.assertEqual(reason, "path-convention")

    def test_mime_match_triggers_outside_tree(self) -> None:
        with _Sandbox() as sbx:
            f = sbx.root / "loose.pdf"
            f.write_bytes(b"%PDF-1.4 stub")
            ok, reason = hook._should_auto_register(f, sbx.session_root)
            self.assertTrue(ok)
            self.assertTrue(reason.startswith("mime:application/pdf"))

    def test_non_artifact_mime_skipped(self) -> None:
        with _Sandbox() as sbx:
            f = sbx.root / "code.py"
            f.write_text("print('hi')\n")
            ok, _reason = hook._should_auto_register(f, sbx.session_root)
            self.assertFalse(ok)

    def test_missing_file_skipped(self) -> None:
        with _Sandbox() as sbx:
            ok, reason = hook._should_auto_register(
                sbx.root / "no-such-file", sbx.session_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "missing")


# ── Session-key resolution ─────────────────────────────────────────────────


class SessionKeyResolutionTests(unittest.TestCase):
    def test_env_var_resolves_to_artifact_root(self) -> None:
        with _Sandbox() as sbx:
            root = hook._resolve_session_root()
            self.assertIsNotNone(root)
            self.assertEqual(root, sbx.session_root)

    def test_unsafe_session_key_returns_none(self) -> None:
        with _Sandbox():
            with mock.patch.dict(os.environ,
                                 {"CORVIN_SESSION_KEY": "../escape"}):
                self.assertIsNone(hook._resolve_session_root())

    def test_missing_env_returns_none(self) -> None:
        with _Sandbox():
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CORVIN_SESSION_KEY", None)
                os.environ.pop("CORVIN_SESSION_KEY", None)
                self.assertIsNone(hook._resolve_session_root())


# ── do_register (synchronous, bypasses fork) ──────────────────────────────


class DoRegisterTests(unittest.TestCase):
    def setUp(self) -> None:
        # Mock the helper-model spawn so tests don't shell out to `claude`.
        self.gen_patch = mock.patch.object(
            hook, "_generate_description",
            return_value="A test PDF describing nothing in particular.")
        self.gen_patch.start()
        # Skip recall-indexing — covered in its own integration test.
        self.idx_patch = mock.patch.object(hook, "_index_in_recall")
        self.idx_patch.start()

    def tearDown(self) -> None:
        self.idx_patch.stop()
        self.gen_patch.stop()

    def test_register_moves_file_into_tree(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.root / "outside.pdf"
            src.write_bytes(b"%PDF-1.4 outside content")
            hook._do_register(source=src, session_root=sbx.session_root,
                              by_tool="tool.Write", reason="mime:application/pdf")
            self.assertFalse(src.exists())  # moved, not copied
            # Manifest line was written.
            manifest = sbx.session_root / ".manifest.jsonl"
            self.assertTrue(manifest.exists())
            entry = json.loads(manifest.read_text().splitlines()[0])
            self.assertEqual(entry["mime"], "application/pdf")
            self.assertEqual(entry["by_tool"], "tool.Write")
            self.assertIn(entry["description"], (
                "A test PDF describing nothing in particular.",
                "",  # Allow empty if redaction wiped the description.
            ))

    def test_register_inside_tree_no_move(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.session_root / "inside.pdf"
            src.write_bytes(b"%PDF-1.4 inside content")
            hook._do_register(source=src, session_root=sbx.session_root,
                              by_tool="tool.Write", reason="path-convention")
            # Source stays (copy, not move).
            self.assertTrue(src.exists())

    def test_register_emits_audit_with_metadata_only(self) -> None:
        with _Sandbox() as sbx:
            src = sbx.root / "x.pdf"
            src.write_bytes(b"%PDF-1.4 z")
            hook._do_register(source=src, session_root=sbx.session_root,
                              by_tool="tool.Write", reason="mime:application/pdf")
            kinds = [e[0] for e in sbx.events]
            self.assertIn("artifact.auto_registered", kinds)
            event = next(e for e in sbx.events
                         if e[0] == "artifact.auto_registered")
            details = event[2]
            # Privacy contract: description must NEVER be in audit details.
            self.assertNotIn("description", details)
            self.assertIn("sha256", details)
            self.assertIn("mime", details)
            self.assertEqual(details["trigger"], "mime:application/pdf")


# ── main() — full hook entry-point ─────────────────────────────────────────


class MainEntryTests(unittest.TestCase):
    def test_unknown_tool_exits_zero(self) -> None:
        with _Sandbox():
            with mock.patch.object(sys, "stdin",
                                   _StubStdin(json.dumps(
                                       {"tool_name": "Read",
                                        "tool_input": {"file_path": "/x"}}))):
                self.assertEqual(hook.main(), 0)

    def test_tool_error_skips_register(self) -> None:
        with _Sandbox() as sbx:
            f = sbx.session_root / "would_register.txt"
            f.write_text("hi")
            payload = json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": str(f)},
                "tool_response": {"error": "boom"},
            })
            with mock.patch.object(sys, "stdin", _StubStdin(payload)), \
                 mock.patch.object(hook, "_spawn_detached") as spawn:
                self.assertEqual(hook.main(), 0)
                spawn.assert_not_called()

    def test_pdf_outside_tree_triggers_register(self) -> None:
        with _Sandbox() as sbx:
            f = sbx.root / "out.pdf"
            f.write_bytes(b"%PDF-1.4 c")
            payload = json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": str(f)},
                "tool_response": {"ok": True},
            })
            with mock.patch.object(sys, "stdin", _StubStdin(payload)), \
                 mock.patch.object(hook, "_spawn_detached") as spawn:
                self.assertEqual(hook.main(), 0)
                spawn.assert_called_once()
                _fn, _args, kwargs = (spawn.call_args.args[0],
                                      spawn.call_args.args[1:],
                                      spawn.call_args.kwargs)
                self.assertEqual(kwargs["by_tool"], "tool.Write")
                self.assertTrue(kwargs["reason"].startswith("mime:"))


# ── helpers ────────────────────────────────────────────────────────────────


class _StubStdin:
    """Minimal stand-in for sys.stdin that returns a fixed payload."""

    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text


if __name__ == "__main__":
    unittest.main()
