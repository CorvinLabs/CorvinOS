"""Tests for Layer 38 — RemoteTriggerReceiver.

Run with: python3 operator/bridges/shared/test_remote_trigger_receiver.py
Target: ≥ 40 test cases (ADR-0048 §Validation).
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
import uuid
from pathlib import Path

# Ensure the shared module path is available
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Patch _forge_se BEFORE importing the module under test so the import-time
# side effects use the mock instead of trying to reach the real forge.
_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})
_patch_forge = mock.patch(
    "remote_trigger_receiver._forge_se", _mock_se,
)
_patch_forge.start()

import remote_trigger_receiver as rtr  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────

HMAC_KEY = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
RECV_KEY = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
ORIGIN_ID = "test-origin"


def _origin_config(hmac_key=HMAC_KEY, recv_key=RECV_KEY, enabled=True, max_ttl_s=300):
    return {
        "origin_id": ORIGIN_ID,
        "hmac_key": hmac_key,
        "recv_key": recv_key,
        "enabled": enabled,
        "max_ttl_s": max_ttl_s,
        "allowed_personas": ["assistant"],
    }


def _write_origin(tmpdir: Path, origin_id: str = ORIGIN_ID, config: dict | None = None) -> Path:
    cfg = config or _origin_config()
    path = tmpdir / f"{origin_id}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    return path


def _build_envelope(
    *,
    hmac_key: str = HMAC_KEY,
    origin_id: str = ORIGIN_ID,
    instruction: str = "echo hello",
    ttl_s: int = 60,
    result_schema: dict | None = None,
    issued_at: float | None = None,
    nonce: str | None = None,
    task_id: str | None = None,
    sender_instance_id: str = "",
    attachments: list | None = None,
    extra_fields: dict | None = None,
) -> dict:
    env: dict = {
        "task_id": task_id or str(uuid.uuid4()),
        "nonce": nonce or secrets.token_hex(32),
        "issued_at": issued_at if issued_at is not None else time.time(),
        "origin_id": origin_id,
        "instruction": instruction,
        "result_schema": result_schema if result_schema is not None else {},
        "ttl_s": ttl_s,
        "sender_instance_id": sender_instance_id,
        "attachments": list(attachments or []),
        "signature": "",
    }
    if extra_fields:
        env.update(extra_fields)
    key = bytes.fromhex(hmac_key)
    payload = {k: v for k, v in env.items() if k != "signature"}
    sig = _hmac.new(
        key,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    env["signature"] = sig
    return env


def _make_receiver(tmpdir: Path) -> rtr.RemoteTriggerReceiver:
    return rtr.RemoteTriggerReceiver(origins_dir=tmpdir)


# ── TaskEnvelope schema tests ─────────────────────────────────────────────

class TestTaskEnvelopeSchema(unittest.TestCase):

    def test_all_fields_present_parses_ok(self):
        d = _build_envelope()
        env = rtr.TaskEnvelope.from_dict(d)
        self.assertEqual(env.origin_id, ORIGIN_ID)

    def test_missing_required_field_raises(self):
        d = _build_envelope()
        del d["task_id"]
        with self.assertRaises(rtr.ValidationError) as ctx:
            rtr.TaskEnvelope.from_dict(d)
        self.assertIn("task_id", ctx.exception.reason)

    def test_wrong_type_issued_at_raises(self):
        d = _build_envelope()
        d["issued_at"] = "not-a-float"
        with self.assertRaises(rtr.ValidationError):
            rtr.TaskEnvelope.from_dict(d)

    def test_wrong_type_result_schema_raises(self):
        d = _build_envelope()
        d["result_schema"] = "not-a-dict"
        with self.assertRaises(rtr.ValidationError):
            rtr.TaskEnvelope.from_dict(d)

    def test_extra_fields_ignored(self):
        d = _build_envelope()
        d["unknown_extra"] = "ignored"
        env = rtr.TaskEnvelope.from_dict(d)
        self.assertFalse(hasattr(env, "unknown_extra"))

    def test_canonical_payload_excludes_signature(self):
        d = _build_envelope()
        env = rtr.TaskEnvelope.from_dict(d)
        payload = json.loads(env.canonical_payload())
        self.assertNotIn("signature", payload)

    def test_canonical_payload_sorted_keys(self):
        d = _build_envelope()
        env = rtr.TaskEnvelope.from_dict(d)
        raw = env.canonical_payload().decode()
        keys = list(json.loads(raw).keys())
        self.assertEqual(keys, sorted(keys))


# ── OriginRegistry tests ──────────────────────────────────────────────────

class TestOriginRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_known_enabled_origin_loads(self):
        _write_origin(self.tmp)
        registry = rtr.OriginRegistry(self.tmp)
        cfg = registry.load(ORIGIN_ID)
        self.assertTrue(cfg["enabled"])

    def test_unknown_origin_raises(self):
        registry = rtr.OriginRegistry(self.tmp)
        with self.assertRaises(rtr.ValidationError) as ctx:
            registry.load("nonexistent")
        self.assertEqual(ctx.exception.reason, "unknown_origin")

    def test_disabled_origin_raises(self):
        _write_origin(self.tmp, config=_origin_config(enabled=False))
        registry = rtr.OriginRegistry(self.tmp)
        with self.assertRaises(rtr.ValidationError) as ctx:
            registry.load(ORIGIN_ID)
        self.assertEqual(ctx.exception.reason, "origin_disabled")

    def test_world_readable_file_raises(self):
        path = _write_origin(self.tmp)
        path.chmod(0o644)
        registry = rtr.OriginRegistry(self.tmp)
        with self.assertRaises(rtr.ValidationError) as ctx:
            registry.load(ORIGIN_ID)
        self.assertEqual(ctx.exception.reason, "origin_file_world_readable")

    def test_per_call_reread_picks_up_change(self):
        path = _write_origin(self.tmp)
        registry = rtr.OriginRegistry(self.tmp)
        cfg1 = registry.load(ORIGIN_ID)
        # Modify file
        new_cfg = _origin_config(max_ttl_s=999)
        path.write_text(json.dumps(new_cfg))
        path.chmod(0o600)
        cfg2 = registry.load(ORIGIN_ID)
        self.assertEqual(cfg1["max_ttl_s"], 300)
        self.assertEqual(cfg2["max_ttl_s"], 999)

    def test_path_traversal_in_origin_id_rejected(self):
        registry = rtr.OriginRegistry(self.tmp)
        for bad_id in ["../etc/passwd", ".hidden", "a/b", "a\\b"]:
            with self.assertRaises(rtr.ValidationError) as ctx:
                registry.load(bad_id)
            self.assertEqual(ctx.exception.reason, "invalid_origin_id", bad_id)

    def test_env_override_for_origins_dir(self):
        _write_origin(self.tmp)
        with mock.patch.dict(os.environ, {"REMOTE_ORIGINS_DIR": str(self.tmp)}):
            registry = rtr.OriginRegistry()
            cfg = registry.load(ORIGIN_ID)
        self.assertTrue(cfg["enabled"])


# ── Time-window tests ─────────────────────────────────────────────────────

class TestTimeWindow(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_in_window_accepted(self):
        env = _build_envelope(issued_at=time.time())
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "ok")

    def test_expired_rejected(self):
        env = _build_envelope(issued_at=time.time() - 400)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_future_rejected(self):
        env = _build_envelope(issued_at=time.time() + 400)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")


# ── Nonce store tests ─────────────────────────────────────────────────────

class TestNonceStore(unittest.TestCase):

    def test_fresh_nonce_accepted(self):
        store = rtr.NonceStore()
        self.assertTrue(store.check_and_add("abc123"))

    def test_same_nonce_replay_rejected(self):
        store = rtr.NonceStore()
        store.check_and_add("abc123")
        self.assertFalse(store.check_and_add("abc123"))

    def test_nonce_expiry_allows_reuse(self):
        store = rtr.NonceStore(ttl_s=0.001)  # 1 ms TTL
        store.check_and_add("abc123")
        time.sleep(0.01)  # wait for expiry
        self.assertTrue(store.check_and_add("abc123"))

    def test_cap_fail_closed_on_valid_entries(self):
        # Fail-closed: when store is full of non-expired entries, new additions
        # must return False — silent LRU eviction would allow replay of still-valid nonces.
        store = rtr.NonceStore()
        for i in range(rtr._NONCE_MAX):
            self.assertTrue(store.check_and_add(f"nonce-{i}"))
        # Store is now full of valid (non-expired) entries — must reject new nonce
        self.assertFalse(store.check_and_add("should-be-rejected"))

    def test_cap_evicts_expired_entries(self):
        # Expired entries ARE evicted to make room for fresh nonces.
        store = rtr.NonceStore(ttl_s=0.001)  # 1 ms TTL
        for i in range(rtr._NONCE_MAX):
            store.check_and_add(f"nonce-{i}")
        time.sleep(0.01)  # wait for all entries to expire
        # Now all entries are expired — a new nonce should be accepted
        self.assertTrue(store.check_and_add("fresh-after-expiry"))

    def test_different_nonces_all_accepted(self):
        store = rtr.NonceStore()
        for i in range(100):
            self.assertTrue(store.check_and_add(f"nonce-{i}"))

    def test_remove_rolls_back_nonce(self):
        # Finding MED-IT4-06: nonce must be removable so receive() can roll
        # back after a failed audit-first write (the nonce was consumed in
        # _validate() before the audit write in receive()).
        store = rtr.NonceStore()
        self.assertTrue(store.check_and_add("rollback-nonce"))
        # Before remove: same nonce rejected as replay
        self.assertFalse(store.check_and_add("rollback-nonce"))
        # Roll back
        store.remove("rollback-nonce")
        # After rollback: nonce is fresh again
        self.assertTrue(store.check_and_add("rollback-nonce"))

    def test_per_origin_quota(self):
        # Finding MED-IT4-07: one origin must not exhaust all global slots.
        store = rtr.NonceStore()
        per_max = rtr.NonceStore._PER_ORIGIN_MAX
        # Fill quota for origin "attacker"
        for i in range(per_max):
            self.assertTrue(
                store.check_and_add(f"atk-{i}", origin_id="attacker"),
                msg=f"slot {i} should be accepted",
            )
        # Next slot from "attacker" must be rejected (quota exhausted)
        self.assertFalse(
            store.check_and_add("atk-overflow", origin_id="attacker"),
            "per-origin quota must block attacker after _PER_ORIGIN_MAX slots",
        )
        # Other origins are unaffected
        self.assertTrue(
            store.check_and_add("victim-nonce", origin_id="victim"),
            "victim origin must still accept nonces when attacker is at quota",
        )


# ── TTL cap tests ─────────────────────────────────────────────────────────

class TestTTLCap(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp, config=_origin_config(max_ttl_s=120))
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ttl_equal_to_max_accepted(self):
        env = _build_envelope(ttl_s=120)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "ok")

    def test_ttl_one_over_max_rejected(self):
        env = _build_envelope(ttl_s=121)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")


# ── Signature tests ───────────────────────────────────────────────────────

class TestSignature(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_correct_hmac_accepted(self):
        env = _build_envelope()
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "ok")

    def test_wrong_key_rejected(self):
        wrong_key = secrets.token_hex(32)
        env = _build_envelope(hmac_key=wrong_key)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_bit_flip_in_signature_rejected(self):
        env = _build_envelope()
        env["signature"] = "00" + env["signature"][2:]  # flip first byte
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_bit_flip_in_payload_rejected(self):
        env = _build_envelope()
        env["instruction"] = env["instruction"] + "X"  # payload changed
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")


# ── Audit-first invariant tests ───────────────────────────────────────────

class TestAuditFirst(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        # Re-pin _forge_se to M1's mock. When both test files are collected
        # by pytest, the M2 file's module-level patch overwrites ours.
        # Re-applying it per-test keeps assertions on _mock_se consistent.
        self._forge_patcher = mock.patch("remote_trigger_receiver._forge_se", _mock_se)
        self._forge_patcher.start()

    def tearDown(self):
        import shutil
        self._forge_patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _mock_se.write_event.reset_mock()

    def test_audit_write_fail_rejects_request(self):
        _mock_se.write_event.side_effect = OSError("disk full")
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")
        _mock_se.write_event.side_effect = None

    def test_envelope_received_emitted_before_response(self):
        call_order: list[str] = []
        _mock_se.write_event.reset_mock()

        original = _mock_se.write_event.side_effect
        def _recording_write(*args, **kwargs):
            call_order.append(kwargs.get("details", {}).get("status", args[1] if len(args) > 1 else ""))
            return {"hash": "abc"}
        _mock_se.write_event.side_effect = _recording_write

        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        receiver.receive(env)

        event_types = [args[1] for args, _ in _mock_se.write_event.call_args_list]
        self.assertIn("A2A.envelope_received", event_types)
        # envelope_received must be first A2A event
        a2a_events = [e for e in event_types if e.startswith("A2A.")]
        self.assertEqual(a2a_events[0], "A2A.envelope_received")
        _mock_se.write_event.side_effect = None


# ── Audit allow-list tests ────────────────────────────────────────────────

class TestAuditAllowList(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self._forge_patcher = mock.patch("remote_trigger_receiver._forge_se", _mock_se)
        self._forge_patcher.start()
        _mock_se.write_event.reset_mock()

    def tearDown(self):
        import shutil
        self._forge_patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _mock_se.write_event.reset_mock()

    def _all_details(self) -> list[dict]:
        return [
            call.kwargs.get("details", call.args[4] if len(call.args) > 4 else {})
            for call in _mock_se.write_event.call_args_list
        ]

    def test_instruction_not_in_audit_details(self):
        receiver = _make_receiver(self.tmp)
        env = _build_envelope(instruction="SECRET_INSTRUCTION")
        receiver.receive(env)
        for details in self._all_details():
            self.assertNotIn("instruction", details, details)

    def test_signature_not_in_audit_details(self):
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        receiver.receive(env)
        for details in self._all_details():
            self.assertNotIn("signature", details, details)

    def test_full_nonce_not_in_audit_details_only_prefix(self):
        receiver = _make_receiver(self.tmp)
        nonce = secrets.token_hex(32)
        env = _build_envelope(nonce=nonce)
        receiver.receive(env)
        for details in self._all_details():
            # Full nonce must not appear
            self.assertNotIn(nonce, str(details))
            # But nonce_prefix (8 chars) may appear
            if "nonce_prefix" in details:
                self.assertEqual(len(details["nonce_prefix"]), 8)


# ── Fail-silent tests ─────────────────────────────────────────────────────

class TestFailSilent(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unknown_origin_returns_rejected_envelope(self):
        env = _build_envelope(origin_id="no-such-origin")
        env["signature"] = "irrelevant"
        resp = self.receiver.receive(env)
        self.assertIsInstance(resp, rtr.ResponseEnvelope)
        self.assertEqual(resp.status, "rejected")

    def test_bad_signature_returns_rejected_envelope(self):
        env = _build_envelope()
        env["signature"] = "0" * 64
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_all_errors_same_rejected_shape(self):
        """All error paths return the same response shape (no oracle attack)."""
        cases = [
            _build_envelope(origin_id="unknown"),   # unknown origin
            {**_build_envelope(), "signature": "0" * 64},  # bad sig
            {**_build_envelope(), "ttl_s": 9999},  # ttl exceeded
        ]
        for env in cases:
            resp = self.receiver.receive(env)
            self.assertEqual(resp.status, "rejected")
            self.assertEqual(resp.data, {})


# ── M1 no-op tests ────────────────────────────────────────────────────────

class TestM1NoOp(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self._forge_patcher = mock.patch("remote_trigger_receiver._forge_se", _mock_se)
        self._forge_patcher.start()
        _mock_se.write_event.reset_mock()

    def tearDown(self):
        import shutil
        self._forge_patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _mock_se.write_event.reset_mock()

    def test_engine_spawned_skipped_m1(self):
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        receiver.receive(env)
        event_types = [
            call.args[1] for call in _mock_se.write_event.call_args_list
        ]
        self.assertIn("A2A.engine_spawned", event_types)
        # find the engine_spawned call details
        for call in _mock_se.write_event.call_args_list:
            if call.args[1] == "A2A.engine_spawned":
                self.assertEqual(call.kwargs["details"]["status"], "skipped_m1")

    def test_response_data_always_empty_in_m1(self):
        receiver = _make_receiver(self.tmp)
        env = _build_envelope(result_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        })
        resp = receiver.receive(env)
        self.assertEqual(resp.data, {})


# ── Result filter tests ───────────────────────────────────────────────────

class TestResultFilter(unittest.TestCase):

    def test_empty_schema_returns_empty(self):
        result = rtr.RemoteTriggerReceiver._filter_result(
            {"key": "value"}, {}
        )
        self.assertEqual(result, {})

    def test_schema_with_no_properties_returns_empty(self):
        result = rtr.RemoteTriggerReceiver._filter_result(
            {"key": "value"}, {"type": "object"}
        )
        self.assertEqual(result, {})

    def test_schema_properties_whitelist(self):
        result = rtr.RemoteTriggerReceiver._filter_result(
            {"allowed": 1, "blocked": 2},
            {"type": "object", "properties": {"allowed": {"type": "integer"}}},
        )
        self.assertEqual(result, {"allowed": 1})

    def test_no_matching_properties_returns_empty(self):
        result = rtr.RemoteTriggerReceiver._filter_result(
            {"foo": 1},
            {"type": "object", "properties": {"bar": {"type": "integer"}}},
        )
        self.assertEqual(result, {})


# ── Response signing tests ────────────────────────────────────────────────

class TestResponseSigning(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_response_has_valid_hmac(self):
        env = _build_envelope()
        resp = self.receiver.receive(env)
        self.assertNotEqual(resp.signature, "")
        # Verify the signature
        key = bytes.fromhex(RECV_KEY)
        d = resp.to_dict()
        d.pop("signature")
        expected = _hmac.new(
            key,
            json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertTrue(_hmac.compare_digest(expected, resp.signature))

    def test_different_task_ids_produce_different_signatures(self):
        resp1 = self.receiver.receive(_build_envelope())
        resp2 = self.receiver.receive(_build_envelope())
        self.assertNotEqual(resp1.signature, resp2.signature)

    def test_rejected_response_has_empty_signature(self):
        resp = self.receiver._rejected_response("tid", "oid")
        self.assertEqual(resp.signature, "")


# ── End-to-end integration tests ─────────────────────────────────────────

class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_valid_envelope_returns_ok(self):
        env = _build_envelope()
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "ok")
        self.assertIsInstance(resp.task_id, str)
        self.assertIsInstance(resp.issued_at, float)

    def test_consecutive_envelopes_with_different_nonces(self):
        for _ in range(5):
            env = _build_envelope()
            resp = self.receiver.receive(env)
            self.assertEqual(resp.status, "ok")

    def test_result_schema_with_valid_properties(self):
        env = _build_envelope(
            result_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.data, {})  # M1: data always {}

    def test_to_dict_round_trips(self):
        env = _build_envelope()
        resp = self.receiver.receive(env)
        d = resp.to_dict()
        self.assertIn("task_id", d)
        self.assertIn("status", d)
        self.assertIn("data", d)
        self.assertIn("signature", d)

    def test_task_id_mirrored_in_response(self):
        tid = str(uuid.uuid4())
        env = _build_envelope(task_id=tid)
        resp = self.receiver.receive(env)
        self.assertEqual(resp.task_id, tid)


# ── ADR-0077 S-2 — Persistent nonce store ────────────────────────────────

class TestPersistentNonceStore(unittest.TestCase):

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "nonces.db"
        from a2a_nonce_store import PersistentNonceStore
        self.store = PersistentNonceStore(self.db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.db.parent, ignore_errors=True)

    def test_fresh_nonce_accepted(self):
        self.assertTrue(self.store.check_and_add("nonce-001"))

    def test_replay_rejected(self):
        self.store.check_and_add("nonce-002")
        self.assertFalse(self.store.check_and_add("nonce-002"))

    def test_different_nonces_independent(self):
        self.assertTrue(self.store.check_and_add("n1"))
        self.assertTrue(self.store.check_and_add("n2"))
        self.assertTrue(self.store.check_and_add("n3"))

    def test_db_mode_0600(self):
        import stat
        self.store.check_and_add("mode-check")
        mode = self.db.stat().st_mode
        self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_survives_new_instance(self):
        # Simulate a process restart: nonce added by first store, rejected by second.
        from a2a_nonce_store import PersistentNonceStore
        self.store.check_and_add("persist-nonce")
        store2 = PersistentNonceStore(self.db)
        self.assertFalse(store2.check_and_add("persist-nonce"))

    def test_fallback_on_bad_path(self):
        from a2a_nonce_store import PersistentNonceStore
        # Unwritable path → falls back to in-memory silently.
        bad = PersistentNonceStore("/root/forbidden/nonces.db")
        self.assertIsNotNone(bad._fallback)
        self.assertTrue(bad.check_and_add("fallback-nonce"))


# ── ADR-0077 S-3 — Per-origin rate limiting ───────────────────────────────

class TestRateLimit(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = _origin_config()
        cfg["rate_limit_rpm"] = 2  # 2 requests per minute = ~1 per 30s
        _write_origin(self.tmp, config=cfg)
        self.receiver = _make_receiver(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_request_allowed(self):
        env = _build_envelope()
        resp = self.receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected")

    def test_rate_limit_triggered_after_burst(self):
        # Drain the bucket (2 tokens).
        for _ in range(2):
            env = _build_envelope()
            self.receiver.receive(env)
        # Third request should be rate-limited.
        env = _build_envelope()
        resp = self.receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_no_rate_limit_when_not_configured(self):
        # Origin without rate_limit_rpm: many requests allowed.
        tmp2 = Path(tempfile.mkdtemp())
        _write_origin(tmp2)  # no rate_limit_rpm
        receiver2 = _make_receiver(tmp2)
        import shutil
        try:
            for _ in range(20):
                env = _build_envelope()
                resp = receiver2.receive(env)
                self.assertNotEqual(resp.status, "rejected",
                                    "should not be rate-limited")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)


# ── ADR-0077 C-2 — Purpose ID gate ───────────────────────────────────────

class TestPurposeIdGate(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_purpose_receiver(self, purposes: list) -> rtr.RemoteTriggerReceiver:
        cfg = _origin_config()
        cfg["allowed_purposes"] = purposes
        _write_origin(self.tmp, config=cfg)
        return _make_receiver(self.tmp)

    def test_no_allowed_purposes_skips_check(self):
        _write_origin(self.tmp)  # no allowed_purposes
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()  # no purpose_id
        resp = receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected")

    def test_allowed_purpose_accepted(self):
        receiver = self._make_purpose_receiver(["compute", "search"])
        env = _build_envelope(extra_fields={"purpose_id": "compute"})
        # Need to re-sign because we added purpose_id
        env = _rebuild_with_purpose(env, "compute")
        resp = receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected")

    def test_unlisted_purpose_rejected(self):
        receiver = self._make_purpose_receiver(["compute"])
        env = _rebuild_with_purpose(_build_envelope(), "analytics")
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_missing_purpose_id_required(self):
        receiver = self._make_purpose_receiver(["compute"])
        env = _build_envelope()  # no purpose_id
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")


def _rebuild_with_purpose(env: dict, purpose: str) -> dict:
    """Rebuild a signed envelope with purpose_id included in the HMAC payload."""
    env2 = {k: v for k, v in env.items() if k != "signature"}
    env2["purpose_id"] = purpose
    payload = {k: v for k, v in env2.items()}
    key = bytes.fromhex(HMAC_KEY)
    sig = _hmac.new(
        key,
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    env2["signature"] = sig
    return env2


# ── ADR-0077 C-5 — Signed rejected responses ─────────────────────────────

class TestSignedRejections(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validation_failure_unsigned_no_recv_key(self):
        # An envelope from an UNKNOWN origin has no recv_key → unsigned.
        receiver = _make_receiver(self.tmp)
        resp = receiver.receive({"task_id": "x", "origin_id": "no-such-origin",
                                  "instruction": "x", "nonce": "x",
                                  "issued_at": time.time(), "result_schema": {},
                                  "ttl_s": 30, "sender_instance_id": "",
                                  "attachments": [], "signature": ""})
        self.assertEqual(resp.status, "rejected")
        self.assertEqual(resp.signature, "")

    def test_injection_rejection_is_signed(self):
        # Injection is detected after origin is loaded → recv_key available → signed.
        # We can't test this in M1 mode (no spawn). Testing via rate-limit rejection
        # which also has recv_key. Build an origin with rate_limit_rpm=0 effectively.
        # Instead, verify via helper: _rejected_response with recv_key produces sig.
        receiver = _make_receiver(self.tmp)
        recv_key = bytes.fromhex(RECV_KEY)
        resp = receiver._rejected_response("tid", ORIGIN_ID, recv_key)
        self.assertNotEqual(resp.signature, "")

    def test_signed_rejection_verifiable(self):
        import hmac as _hmac_mod
        receiver = _make_receiver(self.tmp)
        recv_key = bytes.fromhex(RECV_KEY)
        resp = receiver._rejected_response("tid", ORIGIN_ID, recv_key)
        payload = {k: v for k, v in resp.to_dict().items() if k != "signature"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode()
        expected = _hmac_mod.new(recv_key, canonical, hashlib.sha256).hexdigest()
        self.assertEqual(expected, resp.signature)


# ── ADR-0077 C-6 — Attachment classification gate ────────────────────────

class TestAttachmentClassificationGate(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._forge_patcher = mock.patch("remote_trigger_receiver._forge_se", _mock_se)
        self._forge_patcher.start()
        _mock_se.write_event.reset_mock()

    def tearDown(self):
        import shutil
        self._forge_patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _mock_se.write_event.reset_mock()

    def _make_att_dict(self, classification=None):
        import base64, hashlib
        raw = b"data"
        return {
            "name": "data.csv",
            "mime": "text/csv",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_b64": base64.b64encode(raw).decode(),
            "classification": classification,
        }

    def test_public_attachment_on_internal_cap_allowed(self):
        cfg = _origin_config()
        cfg["max_data_classification"] = "INTERNAL"
        _write_origin(self.tmp, config=cfg)
        receiver = _make_receiver(self.tmp)
        att = self._make_att_dict("PUBLIC")
        env = _build_envelope(attachments=[att])
        resp = receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected")

    def test_confidential_attachment_on_internal_cap_rejected(self):
        cfg = _origin_config()
        cfg["max_data_classification"] = "INTERNAL"
        _write_origin(self.tmp, config=cfg)
        receiver = _make_receiver(self.tmp)
        att = self._make_att_dict("CONFIDENTIAL")
        env = _build_envelope(attachments=[att])
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")

    def test_no_cap_defaults_to_internal(self):
        _write_origin(self.tmp)  # no max_data_classification
        receiver = _make_receiver(self.tmp)
        att = self._make_att_dict("SECRET")
        env = _build_envelope(attachments=[att])
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")


# ── ADR-0103 M4: require_network_attestation enforcement ─────────────────

class TestRequireNetworkAttestation(unittest.TestCase):
    """Verify that require_network_attestation=True in an origin config causes
    the receiver to reject envelopes that arrive without a network_attestation
    block (ADR-0103 M4, corvin_a2a pair waterproofing — ADR-0140).

    No real SesT or RS256 keypair is needed for these tests:
    - Absence tests: envelope simply lacks the field → must be rejected.
    - Presence tests: the RS256 sig check is bypassed via
      CORVIN_A2A_ATTESTATION_DISABLED=1 so we can verify the rest of the
      flow without a network trust anchor.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tmp.chmod(0o700)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_enforced_origin(self, **extra):
        cfg = _origin_config()
        cfg["require_network_attestation"] = True
        cfg.update(extra)
        return _write_origin(self.tmp, config=cfg)

    def test_absent_attestation_rejected_when_required(self):
        """Envelope without network_attestation block must be rejected
        when require_network_attestation=True is set in origin config.
        This is the core ADR-0103 M4 / ADR-0140 waterproofing test.
        """
        self._write_enforced_origin()
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected",
                         "envelope without network_attestation must be rejected")

    def test_absent_attestation_allowed_when_not_required(self):
        """Grace period / offline-pair: require_network_attestation=False
        (or absent) must allow envelopes without a network_attestation block.
        """
        cfg = _origin_config()
        cfg["require_network_attestation"] = False
        _write_origin(self.tmp, config=cfg)
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        resp = receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected",
                            "offline-paired origin must accept envelopes without attestation")

    def test_absent_attestation_allowed_by_default(self):
        """Default (no require_network_attestation field) must not enforce
        attestation — backward-compatible grace period behaviour.
        """
        _write_origin(self.tmp)  # no require_network_attestation key
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        resp = receiver.receive(env)
        self.assertNotEqual(resp.status, "rejected",
                            "origin without require_network_attestation must use grace period")

    def test_signed_rejection_carries_recv_key(self):
        """network_attestation_required is a post-HMAC ValidationError
        (recv_key is known), so the rejection response must be signed.
        An unsigned empty signature would indicate a pre-HMAC failure path.
        """
        self._write_enforced_origin()
        receiver = _make_receiver(self.tmp)
        env = _build_envelope()
        resp = receiver.receive(env)
        self.assertEqual(resp.status, "rejected")
        self.assertNotEqual(resp.signature, "",
                            "network_attestation_required rejection must be signed (post-HMAC)")

    def test_present_attestation_passes_flag_check(self):
        """An envelope WITH a network_attestation block passes the
        require_network_attestation flag check (further validation may fail
        on RS256 sig, but we verify the flag itself does not reject it).
        Test uses CORVIN_A2A_ATTESTATION_DISABLED=1 to skip RS256.
        """
        self._write_enforced_origin()
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        try:
            receiver = _make_receiver(self.tmp)
            env = _build_envelope(extra_fields={
                "network_attestation": {
                    "sest_fp": "a" * 64,
                    "sest_sig": "fakesig",
                    "pairing_id": "test-pair-id",
                    "attested_at": time.time(),
                }
            })
            resp = receiver.receive(env)
            self.assertNotEqual(resp.status, "rejected",
                                "with ATTESTATION_DISABLED the require flag must not block")
        finally:
            os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)


# ── IBC gate tests (COV-001, COV-002, COV-003, ADR-0145) ──────────────────

class TestIBCGates(unittest.TestCase):
    """Verify the three IBC security gates fixed in ADR-0145 iter-2.

    Tests deliberately use trivial forged values — the point is to exercise
    the gate logic, not the crypto primitives (which are tested elsewhere).
    """

    def _make_origins_dir(self, *, require_ibc: bool = False) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="ibc-gate-"))
        cfg = {
            "origin_id": "ibc-test", "hmac_key": "f" * 64, "recv_key": "e" * 64,
            "enabled": True, "max_ttl_s": 300, "allowed_personas": ["assistant"],
            "spawn_worker": False, "require_ibc": require_ibc,
        }
        p = tmp / "ibc-test.json"
        p.write_text(json.dumps(cfg)); p.chmod(0o600)
        return tmp

    def _signed_envelope(self, origins_dir: Path, extra: dict | None = None) -> dict:
        base = {
            "task_id": str(uuid.uuid4()), "nonce": secrets.token_hex(32),
            "issued_at": time.time(),
            "origin_id": "ibc-test", "instruction": "hello", "result_schema": {},
            "ttl_s": 30, "sender_instance_id": "sender-iid", "attachments": [],
            "signature": "",
        }
        if extra:
            base.update(extra)
        payload = {k: v for k, v in base.items() if k != "signature"}
        key = bytes.fromhex("f" * 64)
        base["signature"] = _hmac.new(
            key,
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        return base

    def setUp(self):
        self._patches = []
        self._mock_se2 = mock.MagicMock()
        self._mock_se2.write_event = mock.MagicMock(return_value={"hash": "x"})
        p = mock.patch("remote_trigger_receiver._forge_se", self._mock_se2)
        p.start(); self._patches.append(p)

    def tearDown(self):
        for p in self._patches:
            try: p.stop()
            except RuntimeError: pass

    # COV-001 / IBC-002: require_ibc=True + deps absent + truthy attestation → reject
    def test_require_ibc_true_libs_absent_truthy_att_rejects(self):
        """ADR-0145 iter-2: missing deps + require_ibc=True must fail-closed."""
        import remote_trigger_receiver as rtr
        orig_avail, orig_jwt = rtr._IBC_VERIFY_AVAILABLE, rtr._IBC_JWT_OK
        rtr._IBC_VERIFY_AVAILABLE = False
        try:
            origins_dir = self._make_origins_dir(require_ibc=True)
            env = self._signed_envelope(
                origins_dir,
                extra={"instance_attestation": {"ibc_jti": "j", "ed25519_sig": "s", "ibc_snapshot": "jwt"}},
            )
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertEqual(resp.status, "rejected",
                             "require_ibc=True + absent deps must reject even with truthy attestation")
        finally:
            rtr._IBC_VERIFY_AVAILABLE = orig_avail
            rtr._IBC_JWT_OK = orig_jwt
            shutil.rmtree(str(origins_dir), ignore_errors=True)

    # COV-002: require_ibc=True + attestation absent → reject
    def test_require_ibc_true_absent_attestation_rejects(self):
        """ADR-0145: require_ibc=True with no instance_attestation must reject."""
        import remote_trigger_receiver as rtr
        origins_dir = self._make_origins_dir(require_ibc=True)
        try:
            env = self._signed_envelope(origins_dir)  # no instance_attestation
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertEqual(resp.status, "rejected",
                             "require_ibc=True with absent attestation must reject")
        finally:
            shutil.rmtree(str(origins_dir), ignore_errors=True)

    # COV-002: require_ibc=False + attestation absent → envelope still handled
    def test_require_ibc_false_absent_attestation_not_rejected(self):
        """ADR-0145: require_ibc=False (default) with no attestation must not gate-reject."""
        import remote_trigger_receiver as rtr
        origins_dir = self._make_origins_dir(require_ibc=False)
        try:
            env = self._signed_envelope(origins_dir)  # no instance_attestation
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            # Status may be "ok" or "filtered" (spawn_worker=False → filtered/ok)
            self.assertNotEqual(resp.status, "rejected",
                                "require_ibc=False must not reject on absent attestation")
        finally:
            shutil.rmtree(str(origins_dir), ignore_errors=True)

    # COV-003: HMAC covers instance_attestation — post-signing mutation → bad_signature
    def test_instance_attestation_hmac_covered(self):
        """ADR-0145: mutating instance_attestation after signing must fail HMAC."""
        import remote_trigger_receiver as rtr
        origins_dir = self._make_origins_dir(require_ibc=False)
        try:
            att = {"ibc_jti": "orig_jti", "ed25519_sig": "sig", "ibc_snapshot": "jwt"}
            env = self._signed_envelope(origins_dir, extra={"instance_attestation": att})
            # Now mutate instance_attestation AFTER signing
            env["instance_attestation"] = {"ibc_jti": "swapped", "ed25519_sig": "evil", "ibc_snapshot": "evil_jwt"}
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertEqual(resp.status, "rejected",
                             "Post-signing mutation of instance_attestation must fail HMAC")
        finally:
            shutil.rmtree(str(origins_dir), ignore_errors=True)

    # IBC-003 fix: sub-mismatch always rejects regardless of require_ibc
    def test_ibc_sub_mismatch_always_rejects(self):
        """ADR-0145 iter-2: IBC sub != sender_instance_id must reject even require_ibc=False."""
        import remote_trigger_receiver as rtr
        orig_avail = rtr._IBC_VERIFY_AVAILABLE
        rtr._IBC_VERIFY_AVAILABLE = True
        # Patch _verify_ibc_ed25519 to skip real Ed25519 verification (no real
        # trust anchor in tests) and hand back forged-but-well-formed claims —
        # the point is to exercise the sub-mismatch gate, not the crypto
        # primitive (tested directly in instance_identity's own test suite).
        orig_verify = rtr._verify_ibc_ed25519
        rtr._verify_ibc_ed25519 = lambda *a, **kw: {
            "sub": "DIFFERENT_INSTANCE", "instance_pubkey": "abc",
            "jti": "jti1", "exp": int(time.time()) + 3600,
        }
        origins_dir = self._make_origins_dir(require_ibc=False)
        try:
            # IBC sub is "DIFFERENT_INSTANCE", sender_instance_id is "sender-iid"
            att = {"ibc_jti": "jti1", "ed25519_sig": "sig", "ibc_snapshot": "CORVIN-fake"}
            env = self._signed_envelope(origins_dir, extra={"instance_attestation": att})
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertEqual(resp.status, "rejected",
                             "IBC sub-mismatch must always reject, even require_ibc=False")
        finally:
            rtr._IBC_VERIFY_AVAILABLE = orig_avail
            rtr._verify_ibc_ed25519 = orig_verify
            shutil.rmtree(str(origins_dir), ignore_errors=True)

    # Positive path: a genuinely valid Ed25519-signed IBC + envelope signature
    # must be ACCEPTED — the negative-path tests above only cover rejection,
    # so this is the one test proving the receiver's new Ed25519 verification
    # (instance_identity._verify_ibc_signature, reused via _verify_ibc_ed25519)
    # is actually compatible with what a real sender produces, not just that
    # it rejects forged input.
    def test_valid_ed25519_ibc_and_envelope_signature_accepted(self):
        import base64
        import instance_identity as iid
        from cryptography.hazmat.primitives import serialization as _ser
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        tmp = Path(tempfile.mkdtemp(prefix="ibc-valid-"))
        prev_env = {
            k: os.environ.get(k) for k in (
                "CORVIN_INSTANCE_ID_PATH", "CORVIN_INSTANCE_KEY_PATH",
                "CORVIN_INSTANCE_CERT_PATH",
            )
        }
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(tmp / "instance_id.json")
        os.environ["CORVIN_INSTANCE_KEY_PATH"] = str(tmp / "instance_key.pem")
        os.environ["CORVIN_INSTANCE_CERT_PATH"] = str(tmp / "instance_cert.jwt")

        origins_dir = self._make_origins_dir(require_ibc=True)
        trust_key = Ed25519PrivateKey.generate()
        pub_der = trust_key.public_key().public_bytes(
            _ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo
        )
        trust_pub_b64 = base64.b64encode(pub_der).decode()

        import remote_trigger_receiver as rtr
        orig_ring = dict(iid._IBC_TRUST_KEY_RING)
        iid._IBC_TRUST_KEY_RING.clear()
        iid._IBC_TRUST_KEY_RING["sess-v1"] = trust_pub_b64
        try:
            sender_instance_id = iid.get_instance_id()
            instance_pubkey_b64 = iid.get_instance_pubkey_b64()

            def _b64url(data: bytes) -> str:
                return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

            claims = {
                "type": "instance_binding", "iss": "corvinlabs.io",
                "sub": sender_instance_id, "instance_pubkey": instance_pubkey_b64,
                "jti": "valid-jti-1", "exp": int(time.time()) + 3600,
            }
            header_b64 = _b64url(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": "ibc-v1"}).encode())
            payload_b64 = _b64url(json.dumps(claims).encode())
            sig = trust_key.sign(f"{header_b64}.{payload_b64}".encode())
            ibc_snapshot = f"CORVIN-{header_b64}.{payload_b64}.{_b64url(sig)}"

            task_id, nonce, issued_at = str(uuid.uuid4()), secrets.token_hex(32), time.time()
            instruction = "hello"
            canonical = iid.build_canonical_payload(
                task_id=task_id, origin_id="ibc-test", issued_at=issued_at,
                nonce=nonce, instruction=instruction,
            )
            ed25519_sig = iid.sign_payload(canonical)

            att = {"ibc_jti": "valid-jti-1", "ed25519_sig": ed25519_sig, "ibc_snapshot": ibc_snapshot}
            env = self._signed_envelope(origins_dir, extra={
                "task_id": task_id, "nonce": nonce, "issued_at": issued_at,
                "instruction": instruction, "sender_instance_id": sender_instance_id,
                "instance_attestation": att,
            })
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertNotEqual(
                resp.status, "rejected",
                f"genuinely valid Ed25519 IBC + envelope sig must be accepted, got: {resp}"
            )
        finally:
            iid._IBC_TRUST_KEY_RING.clear()
            iid._IBC_TRUST_KEY_RING.update(orig_ring)
            for k, v in prev_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(str(origins_dir), ignore_errors=True)
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_revoked_ibc_jti_is_rejected_even_when_signature_valid(self):
        # ADR-0145 M3 (IBC-1): a cert whose jti is confirmed on the CRL must be
        # rejected on the receive path even though its Ed25519 signature and
        # expiry are still valid.
        import base64
        import instance_identity as iid
        from cryptography.hazmat.primitives import serialization as _ser
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        tmp = Path(tempfile.mkdtemp(prefix="ibc-revoked-"))
        prev_env = {
            k: os.environ.get(k) for k in (
                "CORVIN_INSTANCE_ID_PATH", "CORVIN_INSTANCE_KEY_PATH",
                "CORVIN_INSTANCE_CERT_PATH",
            )
        }
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(tmp / "instance_id.json")
        os.environ["CORVIN_INSTANCE_KEY_PATH"] = str(tmp / "instance_key.pem")
        os.environ["CORVIN_INSTANCE_CERT_PATH"] = str(tmp / "instance_cert.jwt")

        origins_dir = self._make_origins_dir(require_ibc=True)
        trust_key = Ed25519PrivateKey.generate()
        pub_der = trust_key.public_key().public_bytes(
            _ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo
        )
        trust_pub_b64 = base64.b64encode(pub_der).decode()

        import remote_trigger_receiver as rtr
        orig_ring = dict(iid._IBC_TRUST_KEY_RING)
        orig_crl = rtr._peer_ibc_revoked
        iid._IBC_TRUST_KEY_RING.clear()
        iid._IBC_TRUST_KEY_RING["sess-v1"] = trust_pub_b64
        # CRL says this exact jti is revoked.
        rtr._peer_ibc_revoked = lambda jti, **_kw: jti == "revoked-jti-9"
        try:
            sender_instance_id = iid.get_instance_id()
            instance_pubkey_b64 = iid.get_instance_pubkey_b64()

            def _b64url(data: bytes) -> str:
                return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

            claims = {
                "type": "instance_binding", "iss": "corvinlabs.io",
                "sub": sender_instance_id, "instance_pubkey": instance_pubkey_b64,
                "jti": "revoked-jti-9", "exp": int(time.time()) + 3600,
            }
            header_b64 = _b64url(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": "ibc-v1"}).encode())
            payload_b64 = _b64url(json.dumps(claims).encode())
            sig = trust_key.sign(f"{header_b64}.{payload_b64}".encode())
            ibc_snapshot = f"CORVIN-{header_b64}.{payload_b64}.{_b64url(sig)}"

            task_id, nonce, issued_at = str(uuid.uuid4()), secrets.token_hex(32), time.time()
            instruction = "hello"
            canonical = iid.build_canonical_payload(
                task_id=task_id, origin_id="ibc-test", issued_at=issued_at,
                nonce=nonce, instruction=instruction,
            )
            ed25519_sig = iid.sign_payload(canonical)

            att = {"ibc_jti": "revoked-jti-9", "ed25519_sig": ed25519_sig, "ibc_snapshot": ibc_snapshot}
            env = self._signed_envelope(origins_dir, extra={
                "task_id": task_id, "nonce": nonce, "issued_at": issued_at,
                "instruction": instruction, "sender_instance_id": sender_instance_id,
                "instance_attestation": att,
            })
            recv = rtr.RemoteTriggerReceiver(
                origins_dir=origins_dir, engine_factory=lambda: mock.MagicMock()
            )
            resp = recv.receive(env)
            self.assertEqual(
                resp.status, "rejected",
                f"a revoked IBC jti must be rejected, got: {resp}"
            )
        finally:
            rtr._peer_ibc_revoked = orig_crl
            iid._IBC_TRUST_KEY_RING.clear()
            iid._IBC_TRUST_KEY_RING.update(orig_ring)
            for k, v in prev_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(str(origins_dir), ignore_errors=True)
            shutil.rmtree(str(tmp), ignore_errors=True)


# ── CI lint test ──────────────────────────────────────────────────────────

class TestCILint(unittest.TestCase):

    def test_no_import_anthropic_in_receiver(self):
        import ast
        src = (Path(__file__).resolve().parent / "remote_trigger_receiver.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                self.assertFalse(
                    any("anthropic" in n for n in names),
                    f"Found anthropic import at line {node.lineno}",
                )


if __name__ == "__main__":
    # Stop the module-level forge patch before unittest's own setup (we
    # manage it per-test in setUp/tearDown above), but keep it running
    # during module import so we don't fail on missing forge.
    unittest.main(verbosity=2)
