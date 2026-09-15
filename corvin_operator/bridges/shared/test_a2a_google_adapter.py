"""Unit tests for Layer 38 — GoogleA2AAdapter + GoogleA2ASender.

Tests prove:
  * API key validation (constant-time hash comparison)
  * Instruction extraction from Google A2A message parts
  * File attachment conversion (Google A2A → L38 Attachment)
  * Internal TaskEnvelope signing and routing through RemoteTriggerReceiver
  * ResponseEnvelope → Google A2A Task conversion
  * JSON-RPC dispatch (tasks/send, unsupported methods)
  * Outbound sender: endpoint loading, request building, response parsing
  * Auth failure audit event (A2A.google_auth_failed)
  * World-readable origin config is skipped (fail-closed)

Run: python3 operator/bridges/shared/test_a2a_google_adapter.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})

import a2a_google_adapter as gad  # noqa: E402
import a2a_google_sender as gsd   # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402


# ── Shared key constants ──────────────────────────────────────────────────

HMAC_KEY = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
RECV_KEY = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
API_KEY  = "supersecretkey123"
API_KEY_HASH = hashlib.sha256(API_KEY.encode()).hexdigest()
ORIGIN_ID = "google_a2a.test-agent"


# ── Helpers ───────────────────────────────────────────────────────────────

def _write_origin(tmpdir: Path, origin_id: str = ORIGIN_ID,
                  enabled: bool = True, google_enabled: bool = True,
                  api_key_sha256: str = API_KEY_HASH,
                  spawn_worker: bool = False,
                  mode: int = 0o600) -> Path:
    cfg = {
        "origin_id": origin_id,
        "hmac_key": HMAC_KEY,
        "recv_key": RECV_KEY,
        "enabled": enabled,
        "max_ttl_s": 300,
        "allowed_personas": ["assistant"],
        "spawn_worker": spawn_worker,
        "google_a2a": {
            "enabled": google_enabled,
            "api_key_sha256": api_key_sha256,
        },
    }
    path = tmpdir / f"{origin_id}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(mode)
    return path


def _write_endpoint(tmpdir: Path, endpoint_id: str, url: str,
                    api_key: str = API_KEY,
                    default_ttl_s: int = 60,
                    enabled: bool = True,
                    mode: int = 0o600) -> Path:
    cfg = {
        "endpoint_id": endpoint_id,
        "url": url,
        "enabled": enabled,
        "google_a2a": {
            "api_key": api_key,
            "default_ttl_s": default_ttl_s,
        },
    }
    path = tmpdir / f"{endpoint_id}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(mode)
    return path


def _make_receiver(origins_dir: Path) -> rtr.RemoteTriggerReceiver:
    """Create a receiver with mocked audit for the given origins_dir."""
    return rtr.RemoteTriggerReceiver(
        origins_dir=origins_dir,
        force_m1_only=True,  # no real worker spawn in unit tests
        forge_se=_mock_se,
    )


def _make_adapter(origins_dir: Path,
                  receiver=None,
                  instance_id: str = "test-iid") -> gad.GoogleA2AAdapter:
    if receiver is None:
        receiver = _make_receiver(origins_dir)
    return gad.GoogleA2AAdapter(
        receiver=receiver,
        origins_dir=origins_dir,
        instance_id=instance_id,
        forge_se=_mock_se,
    )


def _google_send_body(
    instruction: str = "hello world",
    task_id: str | None = None,
    parts: list | None = None,
    metadata: dict | None = None,
    jsonrpc_id: str = "req-1",
) -> dict:
    # Use `is not None` so that an explicit empty list is preserved.
    effective_parts = (
        parts if parts is not None
        else [{"type": "text", "text": instruction}]
    )
    return {
        "jsonrpc": "2.0",
        "id": jsonrpc_id,
        "method": "tasks/send",
        "params": {
            **({"id": task_id} if task_id else {}),
            "message": {
                "role": "user",
                "parts": effective_parts,
            },
            **({"metadata": metadata} if metadata else {}),
        },
    }


# ── Test: API key lookup ──────────────────────────────────────────────────

class TestApiKeyLookup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origins = Path(self.tmp) / "origins"
        self.origins.mkdir()

    def test_valid_key_finds_origin(self):
        _write_origin(self.origins)
        adapter = _make_adapter(self.origins)
        cfg = adapter._find_origin_by_api_key(API_KEY)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["origin_id"], ORIGIN_ID)

    def test_wrong_key_returns_none(self):
        _write_origin(self.origins)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key("wrong-key"))

    def test_empty_key_returns_none(self):
        _write_origin(self.origins)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(""))

    def test_none_key_returns_none(self):
        _write_origin(self.origins)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(None))

    def test_disabled_origin_skipped(self):
        _write_origin(self.origins, enabled=False)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(API_KEY))

    def test_google_a2a_disabled_skipped(self):
        _write_origin(self.origins, google_enabled=False)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(API_KEY))

    def test_world_readable_origin_skipped(self):
        _write_origin(self.origins, mode=0o644)
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(API_KEY))

    def test_empty_origins_dir_returns_none(self):
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(API_KEY))

    def test_empty_api_key_sha256_skipped(self):
        _write_origin(self.origins, api_key_sha256="")
        adapter = _make_adapter(self.origins)
        self.assertIsNone(adapter._find_origin_by_api_key(API_KEY))


# ── Test: instruction extraction ─────────────────────────────────────────

class TestExtractInstruction(unittest.TestCase):

    def test_single_text_part(self):
        msg = {"parts": [{"type": "text", "text": "hello"}]}
        self.assertEqual(gad.GoogleA2AAdapter._extract_instruction(msg), "hello")

    def test_multiple_text_parts_joined(self):
        msg = {"parts": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]}
        result = gad.GoogleA2AAdapter._extract_instruction(msg)
        self.assertIn("part one", result)
        self.assertIn("part two", result)

    def test_data_part_serialized_as_json(self):
        msg = {"parts": [{"type": "data", "data": {"key": "value"}}]}
        result = gad.GoogleA2AAdapter._extract_instruction(msg)
        self.assertIn("value", result)
        self.assertIn("key", result)

    def test_file_part_skipped(self):
        msg = {"parts": [
            {"type": "file", "file": {"data": "aGVsbG8=", "mimeType": "text/plain"}},
            {"type": "text", "text": "real instruction"},
        ]}
        result = gad.GoogleA2AAdapter._extract_instruction(msg)
        self.assertEqual(result, "real instruction")

    def test_empty_parts_returns_empty(self):
        self.assertEqual(gad.GoogleA2AAdapter._extract_instruction({}), "")
        self.assertEqual(gad.GoogleA2AAdapter._extract_instruction({"parts": []}), "")

    def test_mixed_parts_with_empty_text_skipped(self):
        msg = {"parts": [
            {"type": "text", "text": "  "},
            {"type": "text", "text": "actual"},
        ]}
        self.assertEqual(gad.GoogleA2AAdapter._extract_instruction(msg), "actual")


# ── Test: attachment extraction ───────────────────────────────────────────

class TestExtractAttachments(unittest.TestCase):

    def test_no_file_parts_returns_empty(self):
        parts = [{"type": "text", "text": "hi"}]
        result = gad.GoogleA2AAdapter._extract_attachments(parts)
        self.assertEqual(result, [])

    def test_file_part_converted(self):
        content = b"hello binary"
        b64 = base64.b64encode(content).decode()
        parts = [{"type": "file", "file": {
            "mimeType": "text/plain",
            "data": b64,
            "name": "hello.txt",
        }}]
        result = gad.GoogleA2AAdapter._extract_attachments(parts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "hello.txt")
        self.assertEqual(result[0].decode(), content)

    def test_invalid_base64_raises(self):
        """Invalid base64 must raise GoogleA2AError, not be silently skipped.

        ADR-0099 iter-2 finding HIGH-A2A-GOOGLE-01: silent skip causes
        audit-log mismatch and hides attacker-controlled data truncation.
        """
        parts = [{"type": "file", "file": {
            "mimeType": "text/plain",
            "data": "!!!!not-base64!!!!",
        }}]
        with self.assertRaises(gad.GoogleA2AError):
            gad.GoogleA2AAdapter._extract_attachments(parts)

    def test_name_sanitized(self):
        content = b"data"
        b64 = base64.b64encode(content).decode()
        parts = [{"type": "file", "file": {
            "mimeType": "application/octet-stream",
            "data": b64,
            "name": "../../../etc/passwd",
        }}]
        result = gad.GoogleA2AAdapter._extract_attachments(parts)
        self.assertEqual(len(result), 1)
        self.assertNotIn("..", result[0].name)
        self.assertNotIn("/", result[0].name)

    def test_empty_data_skipped(self):
        parts = [{"type": "file", "file": {
            "mimeType": "text/plain",
            "data": "",
        }}]
        result = gad.GoogleA2AAdapter._extract_attachments(parts)
        self.assertEqual(result, [])


# ── Test: to_google_task conversion ──────────────────────────────────────

class TestToGoogleTask(unittest.TestCase):

    def _resp(self, status, data=None, attachments=None):
        class _FakeResp:
            pass
        r = _FakeResp()
        r.status = status
        r.data = data or {}
        r.attachments = attachments or []
        return r

    def test_ok_status_yields_completed(self):
        task = gad.GoogleA2AAdapter._to_google_task("t1", self._resp("ok"))
        self.assertEqual(task["status"]["state"], "completed")
        self.assertNotIn("error", task["status"])

    def test_filtered_status_yields_completed(self):
        task = gad.GoogleA2AAdapter._to_google_task("t1", self._resp("filtered"))
        self.assertEqual(task["status"]["state"], "completed")

    def test_rejected_status_yields_failed(self):
        task = gad.GoogleA2AAdapter._to_google_task("t1", self._resp("rejected"))
        self.assertEqual(task["status"]["state"], "failed")
        self.assertIn("error", task["status"])

    def test_timeout_status_yields_failed(self):
        task = gad.GoogleA2AAdapter._to_google_task("t1", self._resp("timeout"))
        self.assertEqual(task["status"]["state"], "failed")

    def test_data_mapped_to_artifact(self):
        resp = self._resp("ok", data={"answer": 42})
        task = gad.GoogleA2AAdapter._to_google_task("t1", resp)
        self.assertEqual(len(task["artifacts"]), 1)
        self.assertEqual(task["artifacts"][0]["name"], "result")
        parts = task["artifacts"][0]["parts"]
        self.assertEqual(parts[0]["type"], "data")
        self.assertEqual(parts[0]["data"]["answer"], 42)

    def test_empty_data_yields_no_artifact(self):
        task = gad.GoogleA2AAdapter._to_google_task("t1", self._resp("ok"))
        self.assertEqual(task["artifacts"], [])

    def test_task_id_propagated(self):
        task = gad.GoogleA2AAdapter._to_google_task("my-task", self._resp("ok"))
        self.assertEqual(task["id"], "my-task")


# ── Test: JSON-RPC dispatch ───────────────────────────────────────────────

class TestDispatch(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origins = Path(self.tmp) / "origins"
        self.origins.mkdir()
        _write_origin(self.origins)
        self.receiver = _make_receiver(self.origins)
        self.adapter = _make_adapter(self.origins, receiver=self.receiver)

    def test_tasks_send_valid_returns_result(self):
        body = _google_send_body("do something useful")
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("result", result)
        self.assertEqual(result["jsonrpc"], "2.0")
        self.assertEqual(result["id"], "req-1")

    def test_tasks_send_wrong_api_key_returns_error(self):
        body = _google_send_body()
        result = self.adapter.dispatch(body, "bad-key")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32001)

    def test_tasks_send_no_api_key_returns_error(self):
        body = _google_send_body()
        result = self.adapter.dispatch(body, None)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32001)

    def test_tasks_send_empty_parts_returns_error(self):
        body = _google_send_body(parts=[])
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32002)

    def test_tasks_send_only_file_parts_returns_error(self):
        # No text/data parts — instruction would be empty
        b64 = base64.b64encode(b"data").decode()
        parts = [{"type": "file", "file": {"data": b64, "mimeType": "text/plain"}}]
        body = _google_send_body(parts=parts)
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32002)

    def test_tasks_get_returns_not_supported(self):
        body = {"jsonrpc": "2.0", "id": "x", "method": "tasks/get",
                "params": {"id": "t1"}}
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32000)

    def test_tasks_cancel_returns_not_supported(self):
        body = {"jsonrpc": "2.0", "id": "x", "method": "tasks/cancel",
                "params": {"id": "t1"}}
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("error", result)

    def test_unknown_method_returns_method_not_found(self):
        body = {"jsonrpc": "2.0", "id": "x", "method": "tasks/subscribe"}
        result = self.adapter.dispatch(body, API_KEY)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], -32601)

    def test_jsonrpc_id_propagated_on_error(self):
        body = _google_send_body(jsonrpc_id="my-req-99")
        result = self.adapter.dispatch(body, "bad-key")
        self.assertEqual(result["id"], "my-req-99")

    def test_auth_failure_emits_audit_event(self):
        _mock_se.write_event.reset_mock()
        body = _google_send_body()
        self.adapter.dispatch(body, "wrong-key")
        calls = [c for c in _mock_se.write_event.call_args_list
                 if "A2A.google_auth_failed" in str(c)]
        self.assertGreater(len(calls), 0, "Expected A2A.google_auth_failed audit event")


# ── Test: agent card ─────────────────────────────────────────────────────

class TestAgentCard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origins = Path(self.tmp) / "origins"
        self.origins.mkdir()
        self.adapter = _make_adapter(self.origins)

    def test_card_has_required_fields(self):
        card = self.adapter.agent_card("http://localhost:8001")
        self.assertIn("name", card)
        self.assertIn("url", card)
        self.assertIn("version", card)
        self.assertIn("capabilities", card)
        self.assertIn("skills", card)

    def test_card_url_uses_base_url(self):
        card = self.adapter.agent_card("http://myhost:9000")
        self.assertEqual(card["url"], "http://myhost:9000/a2a")

    def test_card_url_strips_trailing_slash(self):
        card = self.adapter.agent_card("http://myhost:9000/")
        self.assertEqual(card["url"], "http://myhost:9000/a2a")

    def test_card_overrides_applied(self):
        tmp = Path(tempfile.mkdtemp()) / "origins"
        tmp.mkdir()
        receiver = _make_receiver(tmp)
        adapter = gad.GoogleA2AAdapter(
            receiver=receiver,
            origins_dir=tmp,
            agent_card_overrides={"name": "CustomAgent"},
            forge_se=_mock_se,
        )
        card = adapter.agent_card("http://host")
        self.assertEqual(card["name"], "CustomAgent")


# ── Test: GoogleA2ASender endpoint loading ───────────────────────────────

class TestGoogleA2ASenderEndpoint(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.endpoints = Path(self.tmp) / "endpoints"
        self.endpoints.mkdir()
        self.sender = gsd.GoogleA2ASender(endpoints_dir=self.endpoints, forge_se=_mock_se)

    def test_load_valid_endpoint(self):
        _write_endpoint(self.endpoints, "my-agent", "http://localhost:9000/a2a")
        cfg = self.sender._load_endpoint("my-agent")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["url"], "http://localhost:9000/a2a")

    def test_missing_endpoint_returns_none(self):
        self.assertIsNone(self.sender._load_endpoint("does-not-exist"))

    def test_disabled_endpoint_returns_none(self):
        _write_endpoint(self.endpoints, "dis", "http://x", enabled=False)
        self.assertIsNone(self.sender._load_endpoint("dis"))

    def test_world_readable_endpoint_returns_none(self):
        _write_endpoint(self.endpoints, "world", "http://x", mode=0o644)
        self.assertIsNone(self.sender._load_endpoint("world"))

    def test_no_api_key_returns_none(self):
        cfg = {
            "endpoint_id": "no-key",
            "url": "http://x",
            "enabled": True,
            "google_a2a": {"default_ttl_s": 60},  # no api_key
        }
        path = self.endpoints / "no-key.json"
        path.write_text(json.dumps(cfg))
        path.chmod(0o600)
        self.assertIsNone(self.sender._load_endpoint("no-key"))

    def test_path_traversal_blocked(self):
        self.assertIsNone(self.sender._load_endpoint("../../../etc/passwd"))
        self.assertIsNone(self.sender._load_endpoint(".hidden"))


# ── Test: GoogleA2ASender send (HTTP mocked) ──────────────────────────────

class TestGoogleA2ASenderSend(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.endpoints = Path(self.tmp) / "endpoints"
        self.endpoints.mkdir()
        self.sender = gsd.GoogleA2ASender(endpoints_dir=self.endpoints, forge_se=_mock_se)

    def _mock_response(self, body: dict, status: int = 200):
        raw = json.dumps(body).encode()

        class _FakeResp:
            def __init__(self):
                self.status = status

            def read(self_):
                return raw

            def __enter__(self_):
                return self_

            def __exit__(self_, *args):
                pass

        return _FakeResp()

    def test_send_completed_task(self):
        _write_endpoint(self.endpoints, "agent", "http://mock/a2a")
        resp_body = {
            "jsonrpc": "2.0", "id": "r1",
            "result": {
                "id": "task-1",
                "status": {"state": "completed"},
                "artifacts": [
                    {"name": "result", "parts": [
                        {"type": "data", "data": {"answer": 42}}
                    ]},
                ],
            },
        }
        with mock.patch("urllib.request.urlopen",
                        return_value=self._mock_response(resp_body)):
            result = self.sender.send("agent", "What is 6×7?")

        self.assertTrue(result.ok)
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.data, {"answer": 42})
        self.assertEqual(result.http_status, 200)

    def test_send_text_artifact_extracted(self):
        _write_endpoint(self.endpoints, "agent2", "http://mock/a2a")
        resp_body = {
            "jsonrpc": "2.0", "id": "r2",
            "result": {
                "id": "task-2",
                "status": {"state": "completed"},
                "artifacts": [
                    {"name": "reply", "parts": [{"type": "text", "text": "Hello!"}]},
                ],
            },
        }
        with mock.patch("urllib.request.urlopen",
                        return_value=self._mock_response(resp_body)):
            result = self.sender.send("agent2", "Hi")

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "Hello!")

    def test_send_failed_task(self):
        _write_endpoint(self.endpoints, "agent3", "http://mock/a2a")
        resp_body = {
            "jsonrpc": "2.0", "id": "r3",
            "result": {
                "id": "task-3",
                "status": {
                    "state": "failed",
                    "error": {"code": -1, "message": "Oops"},
                },
                "artifacts": [],
            },
        }
        with mock.patch("urllib.request.urlopen",
                        return_value=self._mock_response(resp_body)):
            result = self.sender.send("agent3", "Break")

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")
        self.assertIsNotNone(result.error)

    def test_send_http_error_returns_failed(self):
        _write_endpoint(self.endpoints, "agent4", "http://mock/a2a")
        import urllib.error
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.HTTPError(
                            None, 403, "Forbidden", {}, None)):
            result = self.sender.send("agent4", "hi")

        self.assertFalse(result.ok)
        self.assertEqual(result.http_status, 403)

    def test_send_transport_error_returns_failed(self):
        _write_endpoint(self.endpoints, "agent5", "http://mock/a2a")
        with mock.patch("urllib.request.urlopen",
                        side_effect=ConnectionError("refused")):
            result = self.sender.send("agent5", "hi")

        self.assertFalse(result.ok)
        self.assertEqual(result.http_status, 0)

    def test_send_invalid_json_response_returns_failed(self):
        _write_endpoint(self.endpoints, "agent6", "http://mock/a2a")

        class _BadResp:
            status = 200
            def read(self): return b"not json{{{"
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with mock.patch("urllib.request.urlopen", return_value=_BadResp()):
            result = self.sender.send("agent6", "hi")

        self.assertFalse(result.ok)

    def test_send_missing_endpoint_returns_failed(self):
        result = self.sender.send("nonexistent", "hi")
        self.assertFalse(result.ok)
        self.assertEqual(result.http_status, 0)

    def test_send_jsonrpc_error_in_response(self):
        _write_endpoint(self.endpoints, "agent7", "http://mock/a2a")
        resp_body = {
            "jsonrpc": "2.0", "id": "r7",
            "error": {"code": -32001, "message": "Unauthorized"},
        }
        with mock.patch("urllib.request.urlopen",
                        return_value=self._mock_response(resp_body)):
            result = self.sender.send("agent7", "hi")

        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], -32001)

    def test_send_emits_audit_events(self):
        _write_endpoint(self.endpoints, "agent8", "http://mock/a2a")
        resp_body = {
            "jsonrpc": "2.0", "id": "r8",
            "result": {"id": "t", "status": {"state": "completed"}, "artifacts": []},
        }
        _mock_se.write_event.reset_mock()
        with mock.patch("urllib.request.urlopen",
                        return_value=self._mock_response(resp_body)):
            self.sender.send("agent8", "test")

        events = [str(c) for c in _mock_se.write_event.call_args_list]
        sent_events = [e for e in events if "google_envelope_sent" in e]
        recv_events = [e for e in events if "google_response_received" in e]
        self.assertGreater(len(sent_events), 0)
        self.assertGreater(len(recv_events), 0)


# ── Test: envelope signing goes through L38 pipeline ─────────────────────

class TestEnvelopeSigning(unittest.TestCase):
    """Prove the adapter builds a correctly signed TaskEnvelope and the
    full L38 receiver pipeline processes it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origins = Path(self.tmp) / "origins"
        self.origins.mkdir()
        _write_origin(self.origins)

    def test_valid_request_routes_through_receiver(self):
        """A valid Google A2A request must produce a non-rejected response."""
        receiver = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins,
            force_m1_only=True,
            forge_se=_mock_se,
        )
        adapter = gad.GoogleA2AAdapter(
            receiver=receiver,
            origins_dir=self.origins,
            instance_id="test-iid",
            forge_se=_mock_se,
        )
        body = _google_send_body("ping")
        result = adapter.dispatch(body, API_KEY)
        self.assertIn("result", result)
        task = result["result"]
        # M1 mode: empty data is normal (no worker spawn)
        self.assertIn(task["status"]["state"], ("completed", "failed"))
        # Should be completed (M1 returns "ok"/"filtered" → completed)
        self.assertEqual(task["status"]["state"], "completed")

    def test_bad_signature_rejected_transparently(self):
        """Tampering with the internal envelope should be detected as a
        receiver-level rejection, not an adapter-level crash."""
        receiver = rtr.RemoteTriggerReceiver(
            origins_dir=self.origins,
            force_m1_only=True,
            forge_se=_mock_se,
        )

        original_receive = receiver.receive
        tampered_calls: list[dict] = []

        def _tampered_receive(env: dict) -> rtr.ResponseEnvelope:
            # Flip one byte of the signature to simulate tampering.
            env = dict(env)
            sig = env.get("signature", "")
            env["signature"] = ("0" if sig[:1] != "0" else "f") + sig[1:]
            tampered_calls.append(env)
            return original_receive(env)

        receiver.receive = _tampered_receive

        adapter = gad.GoogleA2AAdapter(
            receiver=receiver,
            origins_dir=self.origins,
            instance_id="test-iid",
            forge_se=_mock_se,
        )
        body = _google_send_body("ping")
        result = adapter.dispatch(body, API_KEY)
        # The result will be a Google A2A "failed" (receiver rejected)
        self.assertIn("result", result)
        self.assertEqual(result["result"]["status"]["state"], "failed")
        self.assertEqual(len(tampered_calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
