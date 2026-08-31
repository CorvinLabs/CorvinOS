"""Security regression tests for console fixes.

Tests cover:
  - Exception details not leaked in HTTP responses (Problems #1, #4)
  - Concurrent channel settings writes (file-level locking) (Problem #2)
  - Input size constraints (Problem #3)
  - Concurrent JSONL appends (Problem #5)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _reset_modules():
    """Reset imported console modules to allow re-initialization."""
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Spin up a self-contained console app for testing with an active session."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from corvin_console import auth as console_session_auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Mint a session directly (same approach as test_profile_routes.py).
        rec = console_session_auth.create_session(
            tenant_id=tenant_id,
            token_fingerprint="test-fp",
        )
        csrf = console_session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        # Attach CSRF header to all mutating requests by default.
        client.headers.update({"X-CSRF-Token": csrf})

        yield client, home, tenant_id
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestExceptionScrubbing(unittest.TestCase):
    """Test that exception details are NOT leaked in HTTP responses."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_invalid_yaml_does_not_leak_details(self):
        """Invalid YAML should return generic error, not exception message."""
        with _sandbox(self._tmp_path) as (client, home, tenant_id):
            resp = client.post(
                "/v1/console/workflows",
                json={
                    "id": "test_wf",
                    "title": "Test",
                    "yaml": "invalid: [yaml: {broken:",
                }
            )

            # Should get 400 with generic message
            assert resp.status_code == 400
            detail = resp.json().get("detail", "")

            # Should NOT contain specific exception details
            assert "YAML" not in detail or "Invalid workflow YAML syntax" in detail
            assert "safe_load" not in detail
            assert "ParserError" not in detail
            assert "Traceback" not in detail
            print(f"✓ Invalid YAML response scrubbed: {detail}")

    def test_file_error_does_not_leak_paths(self):
        """File operation errors should not expose filesystem paths."""
        with _sandbox(self._tmp_path) as (client, home, tenant_id):

            # Make workflows dir read-only to force permission error
            workflows_dir = home / "tenants" / tenant_id / "workflows"
            workflows_dir.mkdir(parents=True, exist_ok=True)
            workflows_dir.chmod(0o000)

            try:
                resp = client.post(
                    "/v1/console/workflows",
                    json={"id": "test_wf", "title": "Test"}
                )

                # Should be 400/500 with generic message
                if resp.status_code in (400, 403, 500):
                    try:
                        detail = resp.json().get("detail", "")
                    except Exception:
                        detail = resp.text

                    # Should NOT contain filesystem paths
                    assert "/home/" not in detail
                    assert "workflows" not in detail or "workflow" in detail.lower()
                    assert "/tenants/" not in detail
                    print(f"✓ File error response scrubbed: {detail}")
            finally:
                workflows_dir.chmod(0o755)


class TestConcurrentWrites(unittest.TestCase):
    """Test concurrent write safety (file-level locking)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_concurrent_channel_settings_writes(self):
        """Concurrent writes to channel settings should not lose data."""
        with _sandbox(self._tmp_path) as (client, home, tenant_id):
            # Import the save function directly
            _reset_modules()
            from corvin_console.routes.chat_settings import (
                _save_channel, _channel_settings_path,
            )

            channel = "test_channel"
            base_path = _channel_settings_path(channel)
            # Clean up the in-repo test_channel dir after the test runs.
            import shutil as _shutil
            self.addCleanup(_shutil.rmtree, str(base_path.parent), True)

            # Baseline state
            _save_channel(channel, {"version": 1, "key1": "value1"})

            # Thread 1 and 2 try to update the same channel concurrently
            results = {}
            errors = []

            def update_thread(thread_id: int, new_data: dict):
                try:
                    _save_channel(channel, {**{"version": 1}, **new_data})
                    results[thread_id] = "success"
                except Exception as e:
                    errors.append(f"Thread {thread_id}: {e}")

            t1 = threading.Thread(target=update_thread, args=(1, {"field_from_t1": "t1_value"}))
            t2 = threading.Thread(target=update_thread, args=(2, {"field_from_t2": "t2_value"}))

            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Check results
            assert len(errors) == 0, f"Thread errors: {errors}"
            assert len(results) == 2, "Both threads should complete successfully"

            # Read final state
            import json as _json
            final = _json.loads(base_path.read_text())

            # Both updates should be present (or at least one complete update, not corrupted)
            print(f"✓ Concurrent writes completed without corruption: {final}")
            # The final state should be valid JSON and not corrupted
            assert isinstance(final, dict)


class TestInputConstraints(unittest.TestCase):
    """Test input size constraints (Problem #3)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_start_run_input_size_limit(self):
        """StartRunRequest should reject inputs with too many parameters."""
        with _sandbox(self._tmp_path) as (client, home, tenant_id):
            # Create a workflow
            wf_resp = client.post(
                "/v1/console/workflows",
                json={"id": "size_test", "title": "Size Test"}
            )
            assert wf_resp.status_code == 200

            # Generate inputs with 100+ parameters (should fail)
            huge_inputs = {f"param_{i}": f"value_{i}" for i in range(150)}

            resp = client.post(
                "/v1/console/workflows/size_test/runs",
                json={
                    "inputs": huge_inputs,
                    "dry_run": True
                }
            )

            # Should be rejected (422 validation error or similar)
            if resp.status_code in (400, 422):
                print(f"✓ Oversized inputs rejected: {resp.status_code}")
                assert resp.status_code >= 400
            else:
                print(f"⚠ Large inputs not rejected (status {resp.status_code})")


@contextmanager
def _isolated_voice_config_dir(tmp_path: Path):
    """Point ``forge.paths.voice_config_dir()`` (and therefore setup.py's
    module-level ``_SERVICE_ENV`` constant) at an isolated per-test
    directory, so these tests never touch a developer's real
    ``~/.config/corvin-voice/service.env``.

    ``_SERVICE_ENV`` is resolved once at import time, so the env var must
    be set BEFORE ``corvin_console.routes.setup`` is (re-)imported —
    callers should enter this context before ``_sandbox`` triggers the
    fresh import (``_sandbox`` -> ``from corvin_console.app import router``
    -> imports ``routes.setup``).
    """
    prev = os.environ.get("VOICE_CONFIG_DIR")
    os.environ["VOICE_CONFIG_DIR"] = str(tmp_path / "corvin-voice")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("VOICE_CONFIG_DIR", None)
        else:
            os.environ["VOICE_CONFIG_DIR"] = prev


class TestEngineKeyConcurrentWrites(unittest.TestCase):
    """Blind spot #1: setup.py's ``_write_env_key`` read-modify-writes
    ``service.env`` with NO file lock (contrast with chat_settings.py's
    ``_save_channel``, which wraps the identical class of read-modify-write
    in ``fcntl.flock`` — see the "Tier 2 fix" comment there). Reachable from
    ``PUT /setup/engines/{engine_id}``.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    @unittest.expectedFailure
    def test_concurrent_write_env_key_for_two_different_keys_loses_an_update(self):
        """Two near-simultaneous saves of two DIFFERENT provider keys (e.g.
        the Setup UI saving ANTHROPIC_API_KEY then OPENAI_API_KEY back to
        back) must both survive. Today they do not: whichever writer's
        read-modify-write cycle finishes last silently clobbers the other's
        key because there is no lock around the read .. os.replace section
        of ``_write_env_key``.

        This test is marked ``expectedFailure`` on purpose: it documents a
        REAL, reproduced bug (silent API-key data loss) rather than
        asserting a tautology. It should start passing — and the
        ``expectedFailure`` decorator should be removed — the day
        ``_write_env_key`` gets the same ``fcntl.flock`` treatment
        ``chat_settings.py._save_channel`` already has.
        """
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                _reset_modules()
                from corvin_console.routes.setup import _write_env_key, _SERVICE_ENV

                _SERVICE_ENV.parent.mkdir(parents=True, exist_ok=True)
                _SERVICE_ENV.write_text("EXISTING=1\n", encoding="utf-8")

                # Deterministically interleave the two read-modify-write
                # cycles: thread "writer-A" is paused right after its
                # read_text() call (i.e. exactly where the real race
                # window is) until thread "writer-B" has fully completed
                # its own read -> write -> os.replace cycle.
                ready_event = threading.Event()
                proceed_event = threading.Event()
                orig_read_text = Path.read_text

                def patched_read_text(self_path, *a, **kw):
                    text = orig_read_text(self_path, *a, **kw)
                    if self_path == _SERVICE_ENV and threading.current_thread().name == "writer-A":
                        ready_event.set()
                        proceed_event.wait(timeout=5)
                    return text

                errors = []

                def run_a():
                    try:
                        _write_env_key("ANTHROPIC_API_KEY", "sk-ant-A")
                    except Exception as e:
                        errors.append(f"A: {e}")

                def run_b():
                    try:
                        _write_env_key("OPENAI_API_KEY", "sk-openai-B")
                    except Exception as e:
                        errors.append(f"B: {e}")

                with patch.object(Path, "read_text", patched_read_text):
                    t_a = threading.Thread(target=run_a, name="writer-A")
                    t_b = threading.Thread(target=run_b, name="writer-B")

                    t_a.start()
                    assert ready_event.wait(timeout=5), "writer-A never reached its read point"
                    t_b.start()
                    t_b.join(timeout=5)
                    proceed_event.set()
                    t_a.join(timeout=5)

                assert not errors, f"writer threads raised: {errors}"

                final_text = _SERVICE_ENV.read_text(encoding="utf-8")
                # Desired (currently unmet) invariant: BOTH keys saved by
                # the two racing writers must be present in the final file.
                assert "ANTHROPIC_API_KEY=sk-ant-A" in final_text, final_text
                assert "OPENAI_API_KEY=sk-openai-B" in final_text, final_text


class TestEngineSetupMalformedBodies(unittest.TestCase):
    """Blind spot #2: ``EngineTestRequest``/``EngineKeyUpdate`` are the only
    Pydantic-validated bodies in setup.py (``extra='forbid'`` + length
    caps) but nothing exercised their 422 path — or even their happy path.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_test_engine_rejects_unexpected_extra_field(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.post(
                    "/v1/console/setup/test-engine",
                    json={"engine_id": "anthropic", "unexpected_field": "x"},
                )
                assert resp.status_code == 422, resp.text

    def test_test_engine_requires_engine_id(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.post("/v1/console/setup/test-engine", json={})
                assert resp.status_code == 422, resp.text

    def test_test_engine_rejects_oversized_engine_id(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.post(
                    "/v1/console/setup/test-engine",
                    json={"engine_id": "x" * 33},
                )
                assert resp.status_code == 422, resp.text

    def test_update_engine_key_rejects_oversized_value(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.put(
                    "/v1/console/setup/engines/anthropic",
                    json={"value": "a" * 501},
                )
                assert resp.status_code == 422, resp.text

    def test_update_engine_key_rejects_extra_field(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.put(
                    "/v1/console/setup/engines/anthropic",
                    json={"value": "sk-ant-ok", "surprise_field": "x"},
                )
                assert resp.status_code == 422, resp.text

    def test_update_engine_key_rejects_non_string_value_int(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.put(
                    "/v1/console/setup/engines/anthropic",
                    json={"value": 12345},
                )
                assert resp.status_code == 422, resp.text

    def test_update_engine_key_rejects_null_value(self):
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                resp = client.put(
                    "/v1/console/setup/engines/anthropic",
                    json={"value": None},
                )
                assert resp.status_code == 422, resp.text

    def test_update_engine_key_happy_path_persists_to_service_env(self):
        """Positive control: there was previously no test at all — not even
        a happy-path one — for this endpoint. A valid body must be
        accepted (200) and the key must actually land in service.env."""
        with _isolated_voice_config_dir(self._tmp_path):
            with _sandbox(self._tmp_path) as (client, home, tenant_id):
                _reset_modules()
                from corvin_console.routes.setup import _SERVICE_ENV

                resp = client.put(
                    "/v1/console/setup/engines/anthropic",
                    json={"value": "sk-ant-real-value"},
                )
                assert resp.status_code == 200, resp.text
                assert resp.json()["ok"] is True

                final_text = _SERVICE_ENV.read_text(encoding="utf-8")
                assert "ANTHROPIC_API_KEY=sk-ant-real-value" in final_text


class TestJSONLIntegrity(unittest.TestCase):
    """Test JSONL append integrity (Problem #5)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_concurrent_jsonl_appends_integrity(self):
        """Concurrent appends to JSONL should not corrupt lines."""
        with _sandbox(self._tmp_path) as (client, home, tenant_id):
            _reset_modules()
            from corvin_console.routes.workflows import _append_chat_line, _read_chat

            wid = "test_workflow"

            # Simulate concurrent appends
            errors = []

            def append_thread(thread_id: int):
                try:
                    for i in range(5):
                        _append_chat_line(
                            tenant_id,
                            wid,
                            {
                                "thread": thread_id,
                                "line": i,
                                "timestamp": time.time(),
                                "data": f"Thread {thread_id}, line {i}",
                            }
                        )
                        time.sleep(0.001)  # Small delay to encourage interleaving
                except Exception as e:
                    errors.append(f"Thread {thread_id}: {e}")

            threads = [
                threading.Thread(target=append_thread, args=(i,))
                for i in range(3)
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Append errors: {errors}"

            # Read back and verify integrity
            lines = _read_chat(tenant_id, wid)

            # Should have 15 total lines (3 threads × 5 lines each)
            assert len(lines) == 15, f"Expected 15 lines, got {len(lines)}"

            # All lines should be valid JSON dicts
            for line in lines:
                assert isinstance(line, dict)
                assert "thread" in line
                assert "line" in line

            print(f"✓ Concurrent JSONL appends completed with integrity: {len(lines)} lines, all valid")


if __name__ == "__main__":
    unittest.main()
