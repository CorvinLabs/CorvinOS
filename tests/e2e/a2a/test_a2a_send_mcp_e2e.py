"""E2E: a2a_send MCP tool (ADR-2099 Phase 2) — gated, never sends directly.

Two structural halves, both proven through their real call sites (no direct
unit-call-only coverage — see memory feedback-dead-mechanism-needs-call-site-test):

A) The MCP tool (``corvin_operator/forge/forge/mcp_server.py``), driven
   through the real ``MCPServer`` JSON-RPC dispatch (``_handle_tools_list`` /
   ``_handle_tools_call``), never the private handler directly:
   - flag off  -> tool absent from tools/list; a forced tools/call still
     refuses (fail-closed re-check inside the handler)
   - flag on   -> tool present; calling it writes a pending record to disk
     and makes ZERO network calls — it never sends

B) The console confirm/peek routes (``core/console/corvin_console/routes/
   a2a_feed.py``), driven through the real FastAPI router + TestClient (no
   direct function calls — matches test_agent_hub_feed_real_data.py):
   - no session       -> 401 (the CSRF/session dependency is live)
   - real session      -> confirms exactly once (replay is 404)
   - the actual network send only happens from the confirm route, never
     from the MCP tool

Run: python3 -m pytest tests/e2e/a2a/test_a2a_send_mcp_e2e.py -q
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    _REPO / "corvin_operator" / "forge",
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "core" / "console",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_KEYS = ("CORVIN_HOME", "CORVIN_TENANT_ID", "FORGE_ROOT", "VOICE_AUDIT_PATH",
         "CORVIN_AUDIT_ANCHOR_KEY", "XDG_CONFIG_HOME")


class _Sandbox(unittest.TestCase):
    """Same env-sandbox shape as test_mcp_server_audit_chain.py — never
    touches the live install's audit chain or CORVIN_HOME."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="a2a-send-mcp-")
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self._saved = {k: os.environ.get(k) for k in _KEYS}
        self.addCleanup(lambda: [
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            for k, v in self._saved.items()
        ])
        for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH"):
            os.environ.pop(k, None)
        self.home = self.tmp / "home"
        self.home.mkdir()
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(self.tmp / "anchor.key")
        self.ws = self.tmp / "checkout" / ".corvin" / "forge"
        self.ws.mkdir(parents=True)


# ── A) MCP tool gating + staging ───────────────────────────────────────────

class A2ASendToolGatingTests(_Sandbox):
    def _srv(self):
        from forge import mcp_server  # noqa: PLC0415
        out = io.StringIO()
        return mcp_server.MCPServer(self.ws, stdout=out), out, mcp_server

    def test_tool_absent_from_tools_list_when_flag_off(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=False):
            srv._handle_tools_list(msgid=1)
        resp = json.loads(out.getvalue().splitlines()[-1])
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertNotIn("a2a_send", names,
                          "a2a_send must not be advertised when the flag is off")

    def test_tool_present_in_tools_list_when_flag_on(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=True):
            srv._handle_tools_list(msgid=1)
        resp = json.loads(out.getvalue().splitlines()[-1])
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("a2a_send", names)

    def test_call_with_flag_off_is_refused_even_if_attempted(self):
        """Fail-closed re-check: even a client that somehow calls the tool
        name directly (stale tools/list cache, hand-crafted request) is
        refused inside the handler, not just hidden from discovery."""
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=False):
            srv._handle_tools_call(
                msgid=2, params={"name": "a2a_send",
                                  "arguments": {"peer_id": "p1", "text": "hi"}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertTrue(resp.get("result", {}).get("isError"), msg=resp)

    def test_call_with_flag_on_stages_pending_and_makes_no_network_call(self):
        srv, out, mod = self._srv()

        def _blocked_urlopen(*a, **kw):
            raise AssertionError("a2a_send must NEVER touch the network directly")

        import urllib.request
        with patch("corvin_core.feature_flags.is_enabled", return_value=True), \
             patch.object(urllib.request, "urlopen", _blocked_urlopen):
            srv._handle_tools_call(
                msgid=3, params={"name": "a2a_send",
                                  "arguments": {"peer_id": "peer-x", "text": "staged msg"}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertIn("result", resp, msg=resp)
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertTrue(payload["staged"])
        self.assertFalse(payload.get("sent", False))
        pending_id = payload["pending_id"]

        # Verify the pending record actually landed on disk (real call site,
        # not a mocked write) — the SAME dir the confirm route reads from.
        import a2a_chat_pending_send as pending_mod  # noqa: PLC0415
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        rec = pending_mod.peek_pending_send(tenant_global_dir("_default"), pending_id)
        self.assertIsNotNone(rec, "pending record not found at the path the confirm route reads")
        self.assertEqual(rec["peer_id"], "peer-x")
        self.assertEqual(rec["text"], "staged msg")

    def test_missing_peer_id_is_invalid_params(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=True):
            srv._handle_tools_call(
                msgid=4, params={"name": "a2a_send", "arguments": {"text": "hi"}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertIn("error", resp, msg=resp)
        self.assertEqual(resp["error"]["code"], -32602)


# ── B) Console confirm/peek routes, real HTTP ──────────────────────────────

def _fake_record(tenant_id: str = "_default"):
    from corvin_console import auth as session_auth  # noqa: PLC0415
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class A2ASendConfirmRouteTests(_Sandbox):
    def setUp(self) -> None:
        super().setUp()
        (self.home / "origins").mkdir()
        (self.home / "endpoints").mkdir()
        os.environ["REMOTE_ORIGINS_DIR"] = str(self.home / "origins")
        os.environ["REMOTE_ENDPOINTS_DIR"] = str(self.home / "endpoints")

        import a2a_chat_pending_send as pending_mod  # noqa: PLC0415
        from corvin_console.routes import a2a_feed as R  # noqa: PLC0415
        self.pending_mod = pending_mod
        self.R = R

    def _client(self, tenant: str | None = "_default"):
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from corvin_console import deps as console_deps  # noqa: PLC0415

        app = FastAPI()
        app.include_router(self.R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
                lambda: _fake_record(tenant)
            )
        return TestClient(app)

    def _stage(self, *, peer_id: str = "peer-1", text: str = "hello") -> str:
        tenant_dir = self.R._forge_paths.tenant_global_dir("_default")
        rec = self.pending_mod.create_pending_send(tenant_dir, peer_id=peer_id, text=text)
        return rec["pending_id"]

    def _register_peer(self, peer_id: str = "peer-1") -> None:
        # Minimal enabled endpoint so _peers()/can_send resolves true.
        (self.home / "endpoints" / f"{peer_id}.json").write_text(json.dumps({
            "origin_id": peer_id, "url": "http://10.9.9.9:9999",
            "hmac_key": "k", "enabled": True,
        }))
        (self.home / "origins" / f"{peer_id}.json").write_text(json.dumps({
            "origin_id": peer_id, "hmac_key": "k", "recv_key": "k", "enabled": True,
        }))

    def test_confirm_without_session_is_401(self):
        pending_id = self._stage()
        r = self._client(tenant=None).post(f"/v1/console/a2a/feed/send/confirm/{pending_id}")
        self.assertEqual(r.status_code, 401)

    def test_peek_without_session_is_401(self):
        pending_id = self._stage()
        r = self._client(tenant=None).get(f"/v1/console/a2a/feed/send/pending/{pending_id}")
        self.assertEqual(r.status_code, 401)

    def test_peek_with_session_returns_staged_text(self):
        pending_id = self._stage(peer_id="peer-1", text="preview me")
        r = self._client().get(f"/v1/console/a2a/feed/send/pending/{pending_id}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["text"], "preview me")

    def test_confirm_with_real_session_sends_exactly_once(self):
        self._register_peer("peer-1")
        pending_id = self._stage(peer_id="peer-1", text="go")

        sent: list[tuple] = []

        def _fake_send_in_background(peer_id, text, atts, timeout_s):
            sent.append((peer_id, text))

        with patch.object(self.R, "_send_in_background", _fake_send_in_background):
            r1 = self._client().post(f"/v1/console/a2a/feed/send/confirm/{pending_id}")
            self.assertEqual(r1.status_code, 202, r1.text)
            # give the thread pool a moment to run the submitted callable
            self.R._SEND_POOL.shutdown(wait=True, cancel_futures=False)

        self.assertEqual(sent, [("peer-1", "go")],
                          "confirm route must trigger exactly one send with the staged text")

    def test_confirm_is_one_time_use_replay_is_404(self):
        self._register_peer("peer-1")
        pending_id = self._stage(peer_id="peer-1", text="go")
        with patch.object(self.R, "_send_in_background", lambda *a: None):
            r1 = self._client().post(f"/v1/console/a2a/feed/send/confirm/{pending_id}")
            self.assertEqual(r1.status_code, 202)
            r2 = self._client().post(f"/v1/console/a2a/feed/send/confirm/{pending_id}")
            self.assertEqual(r2.status_code, 404, "replay of a confirmed pending must be refused")

    def test_confirm_unknown_pending_is_404(self):
        r = self._client().post("/v1/console/a2a/feed/send/confirm/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_confirm_expired_pending_is_404(self):
        pending_id = self._stage(peer_id="peer-1", text="old")
        tenant_dir = self.R._forge_paths.tenant_global_dir("_default")
        pdir = tenant_dir / "remote_trigger" / "pending_chat_sends"
        pf = pdir / f"{pending_id}.json"
        data = json.loads(pf.read_text())
        data["created_at"] = time.time() - 700
        pf.write_text(json.dumps(data))
        r = self._client().post(f"/v1/console/a2a/feed/send/confirm/{pending_id}")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
