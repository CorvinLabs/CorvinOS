"""Tier 3 integration tests — engine detection API routes (ADR-0125).

Tests GET /v1/console/settings/engine/detect and
POST /v1/console/settings/engine/bootstrap via FastAPI TestClient.
Engine probe calls are fully mocked — no real binaries, no network.

Pattern follows test_local_login.py: spin up FastAPI() + include router,
override auth dependencies directly on the app instance.

Run: uv run python3 -m pytest core/console/tests/test_engine_detect_routes_adr0125.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch


def _poll_bootstrap(tc, timeout: float = 5.0) -> dict:
    """Poll GET /bootstrap/status until the job reaches a terminal state.

    ADR-0125 refresh: POST /bootstrap starts a daemon thread and returns at once
    ({"state": "running"}); the terminal result is fetched from the status
    endpoint. The bootstrap_hermes call is mocked, so the thread finishes almost
    immediately — poll briefly rather than asserting the synchronous result.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = tc.get("/v1/console/settings/engine/bootstrap/status").json()
        if data.get("state") in ("done", "error", "idle"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"bootstrap did not finish within {timeout}s")

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


def _reset_modules():
    # Only purge corvin_console* — it must re-import per CORVIN_HOME sandbox.
    # Do NOT purge the shared engine_detection / hermes_bootstrap singletons:
    # sibling test modules (test_engine_detection_adr0125.py,
    # test_hermes_bootstrap_adr0125.py) bind their symbols at collection time,
    # so deleting these from sys.modules here makes their string-based
    # @patch("engine_detection.…") target a fresh module instance while the
    # test body still calls the originally-bound (unpatched) functions —
    # which then invoke real subprocesses (causing failures + a network hang
    # when the whole suite runs in alphabetical order). The detect_routes
    # tests patch engine_detection/hermes_bootstrap attributes by string at
    # call time, so the shared module staying loaded is correct for them too.
    for key in list(sys.modules):
        if key == "corvin_console" or key.startswith("corvin_console."):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """FastAPI TestClient with an isolated CORVIN_HOME and bypassed auth."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev_home = os.environ.get("CORVIN_HOME")
    prev_tid = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router
        from corvin_console.deps import require_csrf, require_session

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")

        # Build a minimal session record for auth bypass
        mock_session = MagicMock()
        mock_session.username = "test_admin"
        mock_session.tenant_id = "_default"
        mock_session.role = "admin"
        mock_session.sid_fingerprint = "test_fp_0123456789ab"

        app.dependency_overrides[require_session] = lambda: mock_session
        app.dependency_overrides[require_csrf] = lambda: "csrf-ok"

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc
    finally:
        for k, v in [(("CORVIN_HOME"), prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


@contextmanager
def _sandbox_two_tenants(tmp_path: Path):
    """Two TestClients, two tenants, ONE import of corvin_console — so both
    clients hit the exact same module-level ``_BOOTSTRAP_STATE`` dict object
    (the thing under test). Mirrors ``_sandbox`` but authenticates each
    client as a different tenant instead of resetting modules per client."""
    home = tmp_path / "corvin_home"
    tenant_a, tenant_b = "tenant_a", "tenant_b"
    for tid in (tenant_a, tenant_b):
        (home / "tenants" / tid / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tid / "global" / "console" / "sessions").mkdir(parents=True)

    prev_home = os.environ.get("CORVIN_HOME")
    prev_tid = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_a

    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router
        from corvin_console.deps import require_csrf, require_session

        def _make_client(tenant_id: str) -> TestClient:
            app = FastAPI()
            app.include_router(console_router, prefix="/v1/console")
            mock_session = MagicMock()
            mock_session.username = f"test_{tenant_id}"
            mock_session.tenant_id = tenant_id
            mock_session.role = "admin"
            mock_session.sid_fingerprint = f"test_fp_{tenant_id}"
            app.dependency_overrides[require_session] = lambda ms=mock_session: ms
            app.dependency_overrides[require_csrf] = lambda: "csrf-ok"
            return TestClient(app, raise_server_exceptions=False)

        tc_a = _make_client(tenant_a)
        tc_b = _make_client(tenant_b)
        try:
            with tc_a, tc_b:
                yield tc_a, tc_b
        finally:
            tc_a.close()
            tc_b.close()
    finally:
        for k, v in [("CORVIN_HOME", prev_home), ("CORVIN_TENANT_ID", prev_tid)]:
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _make_probe(engine_id, installed=True, auth=True, src="subscription", models=None):
    from engine_detection import EngineProbeResult
    return EngineProbeResult(
        engine_id=engine_id,
        installed=installed,
        authenticated=auth,
        credential_source=src,
        version="1.0.0",
        models=models or [],
    )


# ---------------------------------------------------------------------------
# GET /v1/console/settings/engine/detect
# ---------------------------------------------------------------------------

class TestDetectEndpoint(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_correct_shape(self):
        """Response must have results, recommended_engine, needs_bootstrap."""
        probes = [
            _make_probe("claude_code", src="subscription"),
            _make_probe("hermes", src="config_file"),
        ]
        with _sandbox(self._tmp_path) as tc:
            with (
                patch("engine_detection.detect_all", return_value=probes),
                patch("engine_detection.recommended_engine", return_value="claude_code"),
            ):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertIn("recommended_engine", data)
        self.assertIn("needs_bootstrap", data)
        self.assertIsInstance(data["results"], list)

    def test_results_serialise_engine_fields(self):
        """Each result must include all EngineProbeResult fields."""
        probes = [_make_probe("claude_code", src="subscription", models=[])]
        with _sandbox(self._tmp_path) as tc:
            with (
                patch("engine_detection.detect_all", return_value=probes),
                patch("engine_detection.recommended_engine", return_value="claude_code"),
            ):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        for field in ("engine_id", "installed", "authenticated", "credential_source",
                      "version", "models"):
            self.assertIn(field, result, f"Missing field: {field}")
        self.assertEqual(result["engine_id"], "claude_code")
        self.assertEqual(result["credential_source"], "subscription")

    def test_needs_bootstrap_true_when_no_recommendation(self):
        with _sandbox(self._tmp_path) as tc:
            with (
                patch("engine_detection.detect_all", return_value=[]),
                patch("engine_detection.recommended_engine", return_value=None),
            ):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["needs_bootstrap"])
        self.assertIsNone(data["recommended_engine"])

    def test_needs_bootstrap_false_when_engine_ready(self):
        probes = [_make_probe("claude_code", src="subscription")]
        with _sandbox(self._tmp_path) as tc:
            with (
                patch("engine_detection.detect_all", return_value=probes),
                patch("engine_detection.recommended_engine", return_value="claude_code"),
            ):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["needs_bootstrap"])
        self.assertEqual(data["recommended_engine"], "claude_code")

    def test_graceful_fallback_on_detection_exception(self):
        """If detect_all() raises, endpoint returns empty results + error key (not 500)."""
        with _sandbox(self._tmp_path) as tc:
            with patch("engine_detection.detect_all",
                       side_effect=RuntimeError("detection exploded")):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["results"], [])
        self.assertTrue(data["needs_bootstrap"])
        self.assertIn("error", data)

    def test_unauthenticated_returns_non_200(self):
        """Without auth override, real auth middleware should reject the request."""
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        # NO dependency_overrides — real auth runs

        home = self._tmp_path / "corvin_home"
        (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        os.environ["CORVIN_HOME"] = str(home)
        try:
            with TestClient(app, raise_server_exceptions=False) as tc:
                resp = tc.get("/v1/console/settings/engine/detect")
            # Real auth should reject: 401, 403, or redirect (302/307)
            self.assertIn(resp.status_code, (401, 403, 302, 307))
        finally:
            os.environ.pop("CORVIN_HOME", None)
            _reset_modules()

    def test_hermes_models_list_forwarded(self):
        """Hermes models list must appear in the serialised result."""
        probes = [_make_probe("hermes", src="config_file", models=["qwen3:8b", "qwen3:1.7b"])]
        with _sandbox(self._tmp_path) as tc:
            with (
                patch("engine_detection.detect_all", return_value=probes),
                patch("engine_detection.recommended_engine", return_value="hermes"),
            ):
                resp = tc.get("/v1/console/settings/engine/detect")

        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        self.assertEqual(result["models"], ["qwen3:8b", "qwen3:1.7b"])


# ---------------------------------------------------------------------------
# POST /v1/console/settings/engine/bootstrap
# ---------------------------------------------------------------------------

class TestBootstrapEndpoint(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_bootstrap_success_returns_result(self):
        # ADR-0125 refresh: bootstrap is an async job — POST starts it, the
        # terminal result is polled from /bootstrap/status (security review
        # 2026-06-27 — was asserting the old synchronous contract).
        bootstrap_result = {
            "model_selected": "qwen3:8b",
            "ram_gb": 8.0,
            "ollama_installed": True,
            "model_pulled": True,
            "error": None,
        }
        with _sandbox(self._tmp_path) as tc:
            with patch("hermes_bootstrap.bootstrap_hermes", return_value=bootstrap_result):
                resp = tc.post("/v1/console/settings/engine/bootstrap")
                self.assertEqual(resp.status_code, 200)
                self.assertIn(resp.json()["state"], ("running", "done"))
                final = _poll_bootstrap(tc)

        self.assertEqual(final["state"], "done")
        self.assertEqual(final["result"]["model_selected"], "qwen3:8b")
        self.assertTrue(final["result"]["model_pulled"])
        self.assertIsNone(final["result"]["error"])

    def test_bootstrap_propagates_error_field(self):
        bootstrap_result = {
            "model_selected": "qwen3:8b",
            "ram_gb": 8.0,
            "ollama_installed": False,
            "model_pulled": False,
            "error": "Ollama installation failed",
        }
        with _sandbox(self._tmp_path) as tc:
            with patch("hermes_bootstrap.bootstrap_hermes", return_value=bootstrap_result):
                resp = tc.post("/v1/console/settings/engine/bootstrap")
                self.assertEqual(resp.status_code, 200)
                final = _poll_bootstrap(tc)

        self.assertEqual(final["state"], "error")
        self.assertEqual(final["result"]["error"], "Ollama installation failed")
        self.assertFalse(final["result"]["model_pulled"])

    def test_bootstrap_low_ram_model(self):
        bootstrap_result = {
            "model_selected": "qwen3:1.7b",
            "ram_gb": 3.5,
            "ollama_installed": True,
            "model_pulled": True,
            "error": None,
        }
        with _sandbox(self._tmp_path) as tc:
            with patch("hermes_bootstrap.bootstrap_hermes", return_value=bootstrap_result):
                resp = tc.post("/v1/console/settings/engine/bootstrap")
                self.assertEqual(resp.status_code, 200)
                final = _poll_bootstrap(tc)

        self.assertEqual(final["state"], "done")
        self.assertEqual(final["result"]["model_selected"], "qwen3:1.7b")

    def test_unauthenticated_cannot_bootstrap(self):
        """POST without auth session must fail (not 200)."""
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        # NO dependency_overrides

        home = self._tmp_path / "corvin_home"
        (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        os.environ["CORVIN_HOME"] = str(home)
        try:
            with TestClient(app, raise_server_exceptions=False) as tc:
                resp = tc.post("/v1/console/settings/engine/bootstrap")
            self.assertIn(resp.status_code, (401, 403, 302, 307, 422))
        finally:
            os.environ.pop("CORVIN_HOME", None)
            _reset_modules()


# ---------------------------------------------------------------------------
# Cross-tenant isolation — _BOOTSTRAP_STATE / _BOOTSTRAP_LOCK (ADR-0007)
# ---------------------------------------------------------------------------

class TestBootstrapCrossTenantIsolation(unittest.TestCase):
    """`_BOOTSTRAP_STATE`/`_BOOTSTRAP_LOCK` (routes/engine.py, ~line 802) are a
    single process-wide dict, NOT keyed by tenant_id — even though
    `_run_bootstrap_job(tenant_id)` runs per-caller and writes tenant-specific
    config via `_save_tenant_yaml(tenant_id, ...)`. Both ``POST /bootstrap``
    and ``GET /bootstrap/status`` read/write this same unscoped dict for
    every tenant.

    This is the same bug class already found + fixed for
    `_WELCOME_CHECK_STATE` in routes/setup.py (converted to a tenant-keyed
    dict via `_welcome_state_for(tenant_id)`, with a dedicated regression
    test `test_two_tenants_do_not_clobber_each_others_welcome_check_state`
    in test_setup_welcome_check.py) — `_BOOTSTRAP_STATE` was never swept.

    These tests PIN the *actual* (buggy, leaky) behavior rather than the
    desired isolated behavior, so the suite stays green while the bug is
    documented for a human fix decision (tenant-keying `_BOOTSTRAP_STATE`
    touches the dict shape, `_run_bootstrap_job`, and both endpoints — not a
    safe one-line change to make inline here). Once fixed, these two tests
    must be rewritten to assert isolation instead, mirroring the welcome-check
    fix and its regression test.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_tenant_b_status_poll_leaks_tenant_as_running_job(self):
        """Tenant B calls GET /bootstrap/status WITHOUT ever calling POST
        itself, yet observes tenant A's in-flight job — proving the state
        dict is shared process-wide rather than scoped per tenant."""
        release = threading.Event()
        started = threading.Event()

        def _slow_bootstrap(progress=None):
            started.set()
            release.wait(timeout=5)
            return {
                "model_selected": "qwen3:8b",
                "ram_gb": 8.0,
                "ollama_installed": True,
                "model_pulled": True,
                "error": None,
            }

        try:
            with _sandbox_two_tenants(self._tmp_path) as (tc_a, tc_b):
                with patch("hermes_bootstrap.bootstrap_hermes", side_effect=_slow_bootstrap):
                    resp_a = tc_a.post("/v1/console/settings/engine/bootstrap")
                    self.assertEqual(resp_a.status_code, 200)
                    self.assertTrue(
                        started.wait(timeout=5),
                        "tenant A's bootstrap job never started",
                    )

                    # Tenant B never POSTed — but its GET returns a "running"
                    # job anyway, because _BOOTSTRAP_STATE is not tenant-scoped.
                    status_b = tc_b.get("/v1/console/settings/engine/bootstrap/status")
                    self.assertEqual(status_b.status_code, 200)
                    data_b = status_b.json()
                    self.assertEqual(
                        data_b["state"], "running",
                        "documented bug: tenant B observed a job it never "
                        "started (see _BOOTSTRAP_STATE cross-tenant leak)",
                    )

                    release.set()
                    final_b = _poll_bootstrap(tc_b)
        finally:
            release.set()  # never leave the worker thread blocked past the test

        # B's own poll resolves to A's result — a false "done" outcome for a
        # job B never ran.
        self.assertEqual(final_b["state"], "done")
        self.assertEqual(final_b["result"]["model_selected"], "qwen3:8b")

    def test_tenant_b_post_while_tenant_a_running_never_starts_tenant_bs_own_job(self):
        """Tenant B's POST /bootstrap, issued while tenant A's job is still
        running, short-circuits into A's in-flight state instead of starting
        a job for B — so B's own tenant config is never touched even though
        B is later told the (shared) job is "done"."""
        release = threading.Event()
        started = threading.Event()

        def _slow_bootstrap(progress=None):
            started.set()
            release.wait(timeout=5)
            return {
                "model_selected": "qwen3:8b",
                "ram_gb": 8.0,
                "ollama_installed": True,
                "model_pulled": True,
                "error": None,
            }

        try:
            with _sandbox_two_tenants(self._tmp_path) as (tc_a, tc_b):
                home = self._tmp_path / "corvin_home"
                with patch("hermes_bootstrap.bootstrap_hermes", side_effect=_slow_bootstrap):
                    resp_a = tc_a.post("/v1/console/settings/engine/bootstrap")
                    self.assertEqual(resp_a.status_code, 200)
                    self.assertTrue(
                        started.wait(timeout=5),
                        "tenant A's bootstrap job never started",
                    )

                    # Tenant B POSTs while A's job is running — per the buggy
                    # process-wide short-circuit, this returns A's in-flight
                    # state and does NOT spawn a job for tenant B at all.
                    resp_b = tc_b.post("/v1/console/settings/engine/bootstrap")
                    self.assertEqual(resp_b.status_code, 200)
                    self.assertEqual(resp_b.json()["state"], "running")

                    release.set()
                    _poll_bootstrap(tc_b)
        finally:
            release.set()

        # Tenant A's config was updated by the (single) real worker thread...
        tenant_a_yaml = home / "tenants" / "tenant_a" / "global" / "tenant.corvin.yaml"
        self.assertTrue(tenant_a_yaml.exists())
        self.assertIn("hermes", tenant_a_yaml.read_text())

        # ...but tenant B's own config was never touched, even though B's
        # poll reported state "done" — a false-positive "configured" outcome.
        tenant_b_yaml = home / "tenants" / "tenant_b" / "global" / "tenant.corvin.yaml"
        self.assertFalse(
            tenant_b_yaml.exists(),
            "documented bug: tenant B was told bootstrap finished, but no "
            "tenant.corvin.yaml was ever written for tenant B — the worker "
            "thread only ever ran with tenant A's tenant_id",
        )


if __name__ == "__main__":
    unittest.main()
