"""Layer 33 — end-to-end test.

Exercises the full session-lifecycle in one run:

    register → list → search → get (text + binary) →
    extract → pin → /reset → pinned survives

Uses an in-process ``MCPServer`` instance plus a real filesystem
sandbox so the handler code path is the same one Claude Code drives
in production. The PostToolUse hook is exercised as a real subprocess
(``python3 artifact_register.py < payload.json``) so the fork+detach
codepath is covered end-to-end.

Privacy invariants (load-bearing) are checked by walking the audit
chain produced over the run and asserting that no description text
or artifact content is ever embedded in event details.
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
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "operator" / "forge"))
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

from forge import artifacts as art  # noqa: E402
from forge.mcp_server import MCPServer  # noqa: E402


# ── Sandbox / helpers ──────────────────────────────────────────────────────


class _E2EBox:
    """A full Layer-33 sandbox with a live MCPServer instance."""

    def __enter__(self) -> "_E2EBox":
        self.tmp = tempfile.TemporaryDirectory(prefix="corvin-l33-e2e-")
        self.root = Path(self.tmp.name)
        self.tenant_id = "_default"
        self.session_key = "discord:e2e-chat"
        self.env_patch = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root),
            "CORVIN_TENANT_ID": self.tenant_id,
            "CORVIN_SESSION_KEY": self.session_key,
            "VOICE_AUDIT_PATH": "",
            "FORGE_PERSONA": "",
            "CORVIN_CALLER_PERSONA": "",
        })
        self.env_patch.start()

        # Tenant tree creation — production normally goes through
        # tenant_migrate.py; for tests we lay down what we need.
        self.tenant_home = self.root / "tenants" / self.tenant_id
        self.forge_root = self.tenant_home / "forge"
        self.forge_root.mkdir(parents=True)
        (self.tenant_home / "global" / "forge").mkdir(parents=True)

        # Drive the server through a workspace root the MCPServer expects.
        # We give it a fresh forge-workspace dir so the policy.json is
        # auto-created.
        self.session_workspace = (self.tenant_home / "sessions"
                                  / self.session_key / "forge")
        self.session_workspace.mkdir(parents=True)
        self.session_artifact_root = (self.tenant_home / "sessions"
                                      / self.session_key / "artifacts")
        self.session_artifact_root.mkdir(parents=True)
        self.global_artifact_root = (self.tenant_home / "global" / "artifacts")
        self.global_artifact_root.mkdir(parents=True)

        self.server = MCPServer(self.session_workspace)
        self._msgid = 0
        return self

    def __exit__(self, *exc) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    # In-process JSON-RPC drive: bypass stdio by invoking the dispatch
    # entry-point directly. We capture the response by intercepting
    # ``_send``.
    def call(self, name: str, args: dict) -> dict:
        self._msgid += 1
        captured: list[dict] = []
        with mock.patch.object(self.server, "_send",
                               side_effect=lambda m: captured.append(m)):
            self.server._handle_tools_call(
                self._msgid,
                {"name": name, "arguments": args},
            )
        if not captured:
            raise AssertionError(f"no response from {name}")
        resp = captured[0]
        if "error" in resp:
            return {"error": resp["error"]}
        content = (resp.get("result") or {}).get("content") or []
        if content and isinstance(content[0], dict) and "text" in content[0]:
            try:
                return json.loads(content[0]["text"])
            except json.JSONDecodeError:
                return {"text": content[0]["text"]}
        return resp.get("result") or {}

    def write_artifact_in_session(self, name: str, data: bytes) -> Path:
        p = self.session_artifact_root / name
        p.write_bytes(data)
        return p

    def audit_events(self) -> list[dict]:
        audit_file = self.tenant_home / "global" / "forge" / "audit.jsonl"
        if not audit_file.exists():
            return []
        out: list[dict] = []
        for ln in audit_file.read_text().splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
        return out


# ── End-to-end lifecycle ──────────────────────────────────────────────────


class FullLifecycleTests(unittest.TestCase):
    """register → list → search → get → extract → pin → /reset"""

    def test_full_lifecycle(self) -> None:
        with _E2EBox() as box:
            # 1. Place a PDF in the artifact tree directly and register
            #    via the MCP tool.
            src = box.write_artifact_in_session("budget.pdf",
                                                b"%PDF-1.4 budget content")
            res = box.call("artifact_register", {
                "path": str(src),
                "description": "Q3 budget proposal, 1 page.",
                "tags": ["budget", "q3"],
            })
            self.assertEqual(res.get("registered"), "budget.pdf")
            self.assertEqual(res.get("mime"), "application/pdf")

            # 2. List — sees the entry.
            listed = box.call("artifact_list", {})
            self.assertEqual(len(listed["artifacts"]), 1)
            self.assertEqual(listed["artifacts"][0]["name"], "budget.pdf")

            # 3. Search — substring fallback (no recall-FTS5 in test sandbox).
            hits = box.call("artifact_search", {"query": "budget"})
            self.assertGreaterEqual(len(hits["hits"]), 1)
            self.assertEqual(hits["hits"][0]["name"], "budget.pdf")

            # 4. Get — within max_bytes, returns content.
            got = box.call("artifact_get", {"name": "budget.pdf",
                                            "max_bytes": 1024})
            self.assertEqual(got["mime"], "application/pdf")
            self.assertEqual(got["encoding"], "base64")
            self.assertGreater(len(got["content"]), 0)

            # 5. Pin — moves to global scope.
            pinned = box.call("artifact_pin", {"name": "budget.pdf"})
            self.assertEqual(pinned.get("pinned"), "budget.pdf")
            globals_listed = box.call("artifact_list", {"scope": "global"})
            self.assertEqual(len(globals_listed["artifacts"]), 1)

            # 6. Session purge — pinned must survive.
            count = art.purge_session(box.session_artifact_root)
            self.assertEqual(count, 1)
            self.assertFalse(box.session_artifact_root.exists())
            # Global scope still has it.
            globals_listed_again = box.call("artifact_list",
                                            {"scope": "global"})
            self.assertEqual(len(globals_listed_again["artifacts"]), 1)
            self.assertEqual(globals_listed_again["artifacts"][0]["name"],
                             "budget.pdf")


# ── Token-cap behaviour ───────────────────────────────────────────────────


class TokenCapTests(unittest.TestCase):
    def test_oversized_get_returns_hint_not_content(self) -> None:
        with _E2EBox() as box:
            big = b"%PDF-1.4 " + b"x" * 200_000
            src = box.write_artifact_in_session("big.pdf", big)
            box.call("artifact_register", {"path": str(src),
                                           "description": "big"})
            got = box.call("artifact_get",
                           {"name": "big.pdf", "max_bytes": 1024})
            self.assertTrue(got.get("too_large"))
            self.assertGreater(got.get("size"), 1024)
            self.assertIn("artifact_extract", got.get("hint", ""))


# ── Path-traversal guard ──────────────────────────────────────────────────


class PathTraversalGuardTests(unittest.TestCase):
    def test_register_outside_artifact_root_refused(self) -> None:
        with _E2EBox() as box:
            # File exists but lives outside <session>/artifacts/.
            outside = box.root / "secret.pdf"
            outside.write_bytes(b"%PDF-1.4 secret")
            res = box.call("artifact_register", {"path": str(outside)})
            self.assertIn("error", res)


# ── Extract — line range on text artifact ─────────────────────────────────


class ExtractTests(unittest.TestCase):
    def test_extract_lines(self) -> None:
        with _E2EBox() as box:
            text = "\n".join(f"line-{i}" for i in range(1, 11))
            src = box.write_artifact_in_session("notes.txt", text.encode())
            box.call("artifact_register", {"path": str(src),
                                           "description": "ten lines"})
            res = box.call("artifact_extract", {"name": "notes.txt",
                                                "range": "lines:3-5"})
            self.assertEqual(res["encoding"], "text")
            self.assertIn("line-3", res["content"])
            self.assertIn("line-5", res["content"])
            self.assertNotIn("line-1", res["content"])
            self.assertNotIn("line-6", res["content"])

    def test_extract_meta_no_content(self) -> None:
        with _E2EBox() as box:
            src = box.write_artifact_in_session("a.pdf", b"%PDF-1.4 content")
            box.call("artifact_register", {"path": str(src),
                                           "description": "a"})
            meta = box.call("artifact_extract", {"name": "a.pdf",
                                                 "range": "meta"})
            self.assertEqual(meta["mime"], "application/pdf")
            self.assertIn("sha256", meta)
            # Meta must NOT include file content.
            self.assertNotIn("content", meta)


# ── Audit chain privacy contract ──────────────────────────────────────────


class AuditPrivacyTests(unittest.TestCase):
    def test_no_description_or_content_in_audit_details(self) -> None:
        secret_desc = "GEHEIME-BESCHREIBUNG-MIT-PII"
        secret_content = b"%PDF-1.4 GEHEIM-INHALT-TEST"
        with _E2EBox() as box:
            src = box.write_artifact_in_session("x.pdf", secret_content)
            box.call("artifact_register", {"path": str(src),
                                           "description": secret_desc})
            box.call("artifact_get", {"name": "x.pdf"})
            box.call("artifact_pin", {"name": "x.pdf"})
            art.purge_session(box.session_artifact_root)

            events = box.audit_events()
            self.assertGreater(len(events), 0)
            for ev in events:
                details_str = json.dumps(ev.get("details", {}))
                self.assertNotIn(secret_desc, details_str)
                self.assertNotIn("GEHEIM-INHALT", details_str)


# ── PostToolUse hook end-to-end (real subprocess) ─────────────────────────


class PostToolUseHookE2ETests(unittest.TestCase):
    """Spawns the hook script as a real subprocess to cover the fork+detach
    path. We mock _generate_description via env to avoid shelling out to
    `claude`."""

    def test_hook_auto_registers_pdf_outside_tree(self) -> None:
        with _E2EBox() as box:
            outside = box.root / "loose.pdf"
            outside.write_bytes(b"%PDF-1.4 loose-doc-content")
            payload = json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": str(outside)},
                "tool_response": {"ok": True},
            })
            hook = REPO / "operator" / "voice" / "hooks" / "artifact_register.py"
            env = os.environ.copy()
            # Force description generator to no-op — keeps the test
            # offline.  The hook reads this env in `_generate_description`
            # via the helper-model layer; setting opt-out shorts the call.
            env["CORVIN_HELPER_MODEL"] = "off"
            r = subprocess.run(
                [sys.executable, str(hook)],
                input=payload, capture_output=True, text=True,
                env=env, timeout=10,
            )
            self.assertEqual(r.returncode, 0)
            # Give the detached worker a moment to finish.
            time.sleep(2.5)
            entries = art.list_active(box.session_artifact_root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].name, "loose.pdf")
            self.assertEqual(entries[0].mime, "application/pdf")
            # File moved out of the bare path.
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
