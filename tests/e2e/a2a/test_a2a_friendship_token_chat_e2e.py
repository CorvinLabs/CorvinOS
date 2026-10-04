"""E2E: a2a_friendship_token_create MCP tool (ADR-2216) — gated, never mints
a token directly.

Same structural shape as test_a2a_send_mcp_e2e.py (ADR-2099 Phase 2), proven
through real call sites, not direct unit calls (see memory
feedback-dead-mechanism-needs-call-site-test):

A) The MCP tool (``corvin_operator/forge/forge/mcp_server.py``), driven
   through the real ``MCPServer`` JSON-RPC dispatch:
   - flag off -> tool absent from tools/list; a forced tools/call still
     refuses (fail-closed re-check inside the handler)
   - flag on  -> tool present; calling it writes a pending REQUEST to disk
     with NO key material — it never calls create_friendship_token()

B) The console pending/confirm routes (``core/console/corvin_console/routes/
   a2a_pair.py``), driven through the real FastAPI router + TestClient:
   - no session -> 401 (the require_csrf dependency is live)
   - real session -> confirm mints a REAL token (parseable + HMAC-verifiable
     by a2a_friendship.parse_and_verify) exactly once (replay is 404)
   - the actual key material is only generated from the confirm route,
     never from the MCP tool

Run: python3 -m pytest tests/e2e/a2a/test_a2a_friendship_token_chat_e2e.py -q
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
    """Same env-sandbox shape as test_a2a_send_mcp_e2e.py — never touches
    the live install's audit chain or CORVIN_HOME."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="a2a-friendship-token-mcp-")
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

class FriendshipTokenToolGatingTests(_Sandbox):
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
        self.assertNotIn("a2a_friendship_token_create", names,
                          "a2a_friendship_token_create must not be advertised when the flag is off")

    def test_tool_present_in_tools_list_when_flag_on(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=True):
            srv._handle_tools_list(msgid=1)
        resp = json.loads(out.getvalue().splitlines()[-1])
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("a2a_friendship_token_create", names)

    def test_call_with_flag_off_is_refused_even_if_attempted(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=False):
            srv._handle_tools_call(
                msgid=2, params={"name": "a2a_friendship_token_create", "arguments": {}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertTrue(resp.get("result", {}).get("isError"), msg=resp)

    def test_call_with_flag_on_stages_request_with_no_key_material(self):
        srv, out, mod = self._srv()

        with patch("corvin_core.feature_flags.is_enabled", return_value=True):
            srv._handle_tools_call(
                msgid=3, params={"name": "a2a_friendship_token_create",
                                  "arguments": {"label": "my-friend", "ttl_hours": 24}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertIn("result", resp, msg=resp)
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertTrue(payload["staged"])
        pending_id = payload["pending_id"]

        # Verify the pending record landed on disk with NO key material —
        # the real call site the confirm route reads from, not a mock.
        import a2a_chat_friendship_token as pending_mod  # noqa: PLC0415
        from forge.paths import tenant_global_dir  # noqa: PLC0415
        rec = pending_mod.peek_pending_token_request(tenant_global_dir("_default"), pending_id)
        self.assertIsNotNone(rec, "pending record not found at the path the confirm route reads")
        self.assertEqual(rec["label"], "my-friend")
        self.assertEqual(rec["ttl_hours"], 24)
        self.assertNotIn("key", rec, "the MCP tool must never generate key material")
        self.assertNotIn("token", rec, "the MCP tool must never generate the actual token")

    def test_invalid_ttl_hours_is_invalid_params(self):
        srv, out, mod = self._srv()
        with patch("corvin_core.feature_flags.is_enabled", return_value=True):
            srv._handle_tools_call(
                msgid=4, params={"name": "a2a_friendship_token_create",
                                  "arguments": {"ttl_hours": "not-a-number"}},
            )
        resp = json.loads(out.getvalue().splitlines()[-1])
        self.assertIn("error", resp, msg=resp)
        self.assertEqual(resp["error"]["code"], -32602)


# ── B) Console pending/confirm routes, real HTTP ────────────────────────────

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


class FriendshipTokenConfirmRouteTests(_Sandbox):
    def setUp(self) -> None:
        super().setUp()
        (self.home / "origins").mkdir()
        (self.home / "endpoints").mkdir()
        os.environ["REMOTE_ORIGINS_DIR"] = str(self.home / "origins")
        os.environ["REMOTE_ENDPOINTS_DIR"] = str(self.home / "endpoints")
        os.environ["REMOTE_PENDING_FRIENDSHIPS_DIR"] = str(self.home / "pending_friendships")

        import a2a_chat_friendship_token as pending_mod  # noqa: PLC0415
        from corvin_console.routes import a2a_pair as R  # noqa: PLC0415
        self.pending_mod = pending_mod
        self.R = R
        self._audit = patch.object(R.console_audit, "action_performed")
        self._audit.start()
        self.addCleanup(self._audit.stop)

    def _client(self, tenant: str | None = "_default"):
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        app = FastAPI()
        app.include_router(self.R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[self.R.require_csrf] = lambda: _fake_record(tenant)
        return TestClient(app)

    def _stage(self, *, label: str = "friend-1", ttl_hours: float = 720.0) -> str:
        tenant_dir = self.R._a2a_paths.tenant_global_dir("_default")
        rec = self.pending_mod.create_pending_token_request(tenant_dir, label=label, ttl_hours=ttl_hours)
        return rec["pending_id"]

    def test_confirm_without_session_is_401(self):
        pending_id = self._stage()
        r = self._client(tenant=None).post(
            f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}"
        )
        self.assertEqual(r.status_code, 401)

    def test_peek_without_session_is_401(self):
        pending_id = self._stage()
        r = self._client(tenant=None).get(
            f"/v1/console/remote-trigger/pair/friendship-token/pending/{pending_id}"
        )
        self.assertEqual(r.status_code, 401)

    def test_peek_with_session_returns_staged_label(self):
        pending_id = self._stage(label="preview-friend")
        r = self._client().get(
            f"/v1/console/remote-trigger/pair/friendship-token/pending/{pending_id}"
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "preview-friend")

    def test_list_pending_without_session_is_401(self):
        self._stage()
        r = self._client(tenant=None).get("/v1/console/remote-trigger/pair/friendship-token/pending")
        self.assertEqual(r.status_code, 401)

    def test_list_pending_returns_staged_items_newest_first(self):
        first = self._stage(label="older")
        second = self._stage(label="newer")
        r = self._client().get("/v1/console/remote-trigger/pair/friendship-token/pending")
        self.assertEqual(r.status_code, 200, r.text)
        ids = [item["pending_id"] for item in r.json()]
        self.assertEqual(ids, [second, first])

    def test_list_pending_excludes_confirmed_item(self):
        pending_id = self._stage(label="once")
        self._client().post(f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}")
        r = self._client().get("/v1/console/remote-trigger/pair/friendship-token/pending")
        self.assertEqual(r.json(), [])

    def test_confirm_with_real_session_mints_a_real_verifiable_token(self):
        pending_id = self._stage(label="go-friend", ttl_hours=24)
        r = self._client().post(
            f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}"
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["label"], "go-friend")
        self.assertTrue(body["token"].startswith("corvin-a2a:ft1:"))

        # Real call site proof: the returned token round-trips through the
        # SAME verifier a receiving instance would use — not a stub shape.
        import a2a_friendship as ft  # noqa: PLC0415
        parsed = ft.parse_and_verify(body["token"])
        self.assertEqual(parsed.label, "go-friend")
        self.assertEqual(parsed.kid, body["kid"])

    def test_confirm_is_one_time_use_replay_is_404(self):
        pending_id = self._stage(label="once")
        r1 = self._client().post(
            f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}"
        )
        self.assertEqual(r1.status_code, 200)
        r2 = self._client().post(
            f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}"
        )
        self.assertEqual(r2.status_code, 404, "replay of a confirmed pending must be refused")

    def test_confirm_unknown_pending_is_404(self):
        r = self._client().post(
            "/v1/console/remote-trigger/pair/friendship-token/confirm/does-not-exist"
        )
        self.assertEqual(r.status_code, 404)

    def test_confirm_expired_pending_is_404(self):
        pending_id = self._stage(label="old")
        tenant_dir = self.R._a2a_paths.tenant_global_dir("_default")
        pdir = tenant_dir / "remote_trigger" / "pending_chat_friendship_tokens"
        pf = pdir / f"{pending_id}.json"
        data = json.loads(pf.read_text())
        data["created_at"] = time.time() - 700
        pf.write_text(json.dumps(data))
        r = self._client().post(
            f"/v1/console/remote-trigger/pair/friendship-token/confirm/{pending_id}"
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
