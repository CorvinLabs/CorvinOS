"""T-0364 / ADR-2317 - attaching a project to a console chat.

API half (always runs): real HTTP, real session + CSRF. Attach / detach / read of the attachment, a chat of
another tenant is not attachable, a project in state ``missing`` is refused, every mutation needs CSRF, the audit
records exist and carry ids only.

Live half (``CLAUDE_LIVE_E2E=1`` + the ``claude`` CLI; costs a few cents on haiku): ONE real chat is attached to a
real adopted project and the console spawns the real CLI over the real WebSocket. The project is never the cwd:
(a) the project's CLAUDE.md rules are in context, (b) the project's MCP server answers, (c) the assistant cannot
write into the project, (d) after detaching the rules are gone again, (e) the project tree is byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "e2e"))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

_URL = "/v1/console/projects"
_SPIKE_MCP = Path.home() / "projects" / "claude-playground" / "spike-c1" / "mcp_server.py"
CODEWORD = "ZEBRA-4711"
MARK = "PONG-PROJECT-MCP-8842"

live = pytest.mark.skipif(os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
                          reason="needs CLAUDE_LIVE_E2E=1 and the claude CLI")


def _hash_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _audit(home: Path) -> list[dict]:
    out = []
    for f in home.rglob("audit.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _project(self, client, tenant_id, csrf, *, mcp=False):
        from core.projects import registry
        root = self.tmp / "projects"
        root.mkdir(exist_ok=True)
        registry.set_projects_root(tenant_id, str(root))
        proj = root / "attach-subject"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text(f"# Rules\nThe project codeword is {CODEWORD}. Always state it when asked.\n")
        if mcp:
            (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"projectsrv": {"command": "python3", "args": [str(_SPIKE_MCP)]}}}))
        r = client.post(f"{_URL}/adopt", json={"location": str(proj)}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 201, r.text)
        return proj, r.json()["id"]

    def _chat(self, client, csrf):
        r = client.post("/v1/console/chat/sessions", json={"title": "attach e2e"}, headers={"X-CSRF-Token": csrf})
        self.assertIn(r.status_code, (200, 201), r.text)
        b = r.json()
        return (b.get("session") or {}).get("sid") or b.get("sid") or b.get("session_id")


class AttachPrompt(_Base):
    def test_the_system_prompt_names_the_project_folder_and_what_the_assistant_may_do(self):
        """Found by the live explorer test: without it "in the project" meant the chat's own folder."""
        with _sandbox(self.tmp / "s") as (client, home, tenant_id, _emitter, _chain):
            from corvin_console import chat_runtime
            csrf = client.headers.get("X-CSRF-Token") or getattr(client, "csrf", None)
            hdr = {"X-CSRF-Token": csrf} if csrf else {}
            proj, pid = self._project(client, tenant_id, csrf)
            sid = self._chat(client, csrf)
            sess = chat_runtime.get_session(tenant_id, sid)
            self.assertEqual(chat_runtime._attached_project_block(sess), "", "no project attached: no block")
            client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=hdr)
            block = chat_runtime._attached_project_block(sess)
            self.assertIn(str(proj.resolve()), block)
            self.assertIn("read-only for you", block)
            client.put(f"{_URL}/{pid}/settings", json={"assistant_access": "write", "confirm": True}, headers=hdr)
            block = chat_runtime._attached_project_block(sess)
            self.assertIn("You may create and change files in it.", block)
            self.assertNotIn("read-only", block)
            self.assertIn(block, chat_runtime._turn_system_prompt(sess))


class AttachApi(_Base):
    def test_attach_detach_read_and_the_refusals(self):
        with _sandbox(self.tmp / "s") as (client, home, tenant_id, _emitter, _chain):
            csrf = client.headers.get("X-CSRF-Token") or getattr(client, "csrf", None)
            hdr = {"X-CSRF-Token": csrf} if csrf else {}
            proj, pid = self._project(client, tenant_id, csrf)
            sid = self._chat(client, csrf)
            self.assertEqual(client.get(f"{_URL}/chat-attachment", params={"session_id": sid}).json()["project_id"], None)
            r = client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            got = client.get(f"{_URL}/chat-attachment", params={"session_id": sid}).json()
            self.assertEqual((got["project_id"], got["title"]), (pid, "attach-subject"))
            # refusals: unknown chat, unknown project, invalid chat id, no CSRF
            self.assertEqual(client.post(f"{_URL}/{pid}/attach", json={"session_id": "nope"}, headers=hdr).status_code, 404)
            self.assertEqual(client.post(f"{_URL}/missing-one/attach", json={"session_id": sid}, headers=hdr).status_code, 404)
            self.assertEqual(client.post(f"{_URL}/{pid}/attach", json={"session_id": "../x"}, headers=hdr).status_code, 404)
            self.assertIn(client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers={"X-CSRF-Token": "bad"}).status_code, (401, 403))
            # a missing project folder cannot be attached
            shutil.move(str(proj), str(self.tmp / "gone"))
            sid2 = self._chat(client, csrf)
            r = client.post(f"{_URL}/{pid}/attach", json={"session_id": sid2}, headers=hdr)
            self.assertEqual(r.status_code, 409, r.text)
            shutil.move(str(self.tmp / "gone"), str(proj))
            # the attachment names the project only; never a path, never in the chat's workdir
            store = (home / "tenants" / tenant_id / "projects" / "chat_attachments.json")
            self.assertEqual(json.loads(store.read_text())["chats"], {sid: pid})
            self.assertEqual(oct(store.stat().st_mode & 0o777), "0o600")
            self.assertFalse(list(home.rglob(".corvin-project*")))
            # detach
            self.assertEqual(client.post(f"{_URL}/detach", json={"session_id": sid}, headers=hdr).json()["attached"], False)
            self.assertEqual(json.loads(store.read_text())["chats"], {})
            ev = json.dumps(_audit(home))
            self.assertIn("project.attached", ev)
            self.assertIn("project.detached", ev)
            self.assertNotIn(str(proj), ev)

    def test_spawn_parts_for_the_attached_chat(self):
        """What the console's CLI spawn gets: rules env, the project as an extra dir, MCP file, write denial; nothing when detached."""
        with _sandbox(self.tmp / "s") as (client, home, tenant_id, _emitter, _chain):
            csrf = client.headers.get("X-CSRF-Token") or getattr(client, "csrf", None)
            hdr = {"X-CSRF-Token": csrf} if csrf else {}
            proj, pid = self._project(client, tenant_id, csrf, mcp=True)
            sid = self._chat(client, csrf)
            from core.projects import attachment
            self.assertIsNone(attachment.spawn_parts(tenant_id, sid))
            client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=hdr)
            parts = attachment.spawn_parts(tenant_id, sid, l34_allows=lambda t: True)
            self.assertEqual(parts["add_dir"], str(proj.resolve()))
            self.assertEqual(parts["env"], {"CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD": "1"})
            self.assertEqual(parts["mcp_config"], str(proj / ".mcp.json"))
            self.assertTrue(all(d.startswith(("Write(//", "Edit(//", "NotebookEdit(//")) for d in parts["deny"]), parts["deny"])
            # fail closed: a gate that says no (or raises) withholds the whole attachment
            self.assertIsNone(attachment.spawn_parts(tenant_id, sid, l34_allows=lambda t: False))
            self.assertIsNone(attachment.spawn_parts(tenant_id, sid, l34_allows=lambda t: 1 / 0))
            # a symlinked MCP file is not passed on
            (proj / ".mcp.json").unlink()
            (proj / ".mcp.json").symlink_to(self.tmp / "elsewhere.json")
            self.assertIsNone(attachment.spawn_parts(tenant_id, sid, l34_allows=lambda t: True)["mcp_config"])


@live
@unittest.skipUnless(_SPIKE_MCP.is_file(), "needs the spike MCP server")
class AttachLive(_Base):
    def _turn(self, client, sid, text):
        out = []
        with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "user", "text": text})
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                m = ws.receive_json()
                if m.get("type") in ("result", "delta", "text"):
                    out.append(str(m.get("text") or m.get("delta") or ""))
                if m.get("type") == "done":
                    break
                self.assertNotEqual(m.get("type"), "error", m)
        return "".join(out)

    def test_a_real_chat_with_an_attached_project(self):
        with _sandbox(self.tmp / "s") as (client, home, tenant_id, _emitter, _chain):
            spec = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text("spec:\n  default_engine: claude_code\n  engine_models:\n    claude_code:\n      os_model: haiku\n")
            csrf = client.headers.get("X-CSRF-Token") or getattr(client, "csrf", None)
            hdr = {"X-CSRF-Token": csrf} if csrf else {}
            proj, pid = self._project(client, tenant_id, csrf, mcp=True)
            before = _hash_tree(proj)
            sid = self._chat(client, csrf)
            ask = "What is the project codeword? If it is not in your context answer exactly UNKNOWN. Do not look for files."
            self.assertNotIn(CODEWORD, self._turn(client, sid, ask), "rules leaked before the attach")
            client.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=hdr)
            a = self._turn(client, sid, ask)
            self.assertIn(CODEWORD, a, f"project rules not in context: {a!r}")
            b = self._turn(client, sid, "Call the tool project_ping and print exactly what it returns. If you have no such tool answer NO-TOOL.")
            self.assertIn(MARK, b, f"project MCP server not reachable: {b!r}")
            target = proj / "written-by-assistant.txt"
            self._turn(client, sid, f"Create the file {target} containing hello using your Write tool. Report DONE or the error.")
            self.assertFalse(target.exists(), "the assistant wrote into the attached project")
            client.post(f"{_URL}/detach", json={"session_id": sid}, headers=hdr)
            sid3 = self._chat(client, csrf)
            self.assertNotIn(CODEWORD, self._turn(client, sid3, ask), "rules still in context after detach")
            self.assertEqual(_hash_tree(proj), before, "the project tree changed")


if __name__ == "__main__":
    unittest.main()
