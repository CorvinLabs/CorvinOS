"""Per-subtask E2E for ADR-0007 Phase 2.3 — engine-dispatch lifecycle.

Covers:
  * Happy path: POST → background dispatch → GET shows ``completed``
    with non-empty ``result.final_text`` from the fake engine.
  * Engine failure: a stub engine yielding ``StreamEvent(type="error")``
    moves the run to ``failed`` with the engine's diagnostic in
    ``record.error``.
  * Engine exception: a stub engine raising on iteration moves the
    run to ``failed`` with the exception class + message.
  * Budget timeout: a stub engine that sleeps past
    ``budget_override.max_wall_clock_s`` moves the run to
    ``budget_exceeded`` with a wall-clock diagnostic.
  * Tenant-env propagation: the stub engine records the env it
    receives; the test asserts ``CORVIN_TENANT_ID`` matches the URL
    tenant.
  * Cross-tenant isolation under dispatch: two tenants run concurrently;
    each one's record only references its own tenant.
  * Dispatcher drain on lifespan shutdown: the ``with`` exit awaits
    the in-flight dispatcher.

The happy-path case uses the real :class:`ClaudeCodeEngine` with the
``ADAPTER_FAKE_CLAUDE=1`` fixture so the engine layer is exercised
end-to-end without spending API credits.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# Make the in-tree corvin-gateway + forge packages importable when
# running this file directly.
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
# voice/bridges/shared/ — for the agents.claude_code engine
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from agents import StreamEvent  # noqa: E402


# ── Common fixtures ──────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-disp-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.02"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)


@contextmanager
def gateway_client(engine_factory=None, default_budget_s: int = 60):
    """Engage the FastAPI lifespan with an optional injected engine.

    Resets ``app.state.dispatcher`` after the test so subsequent
    test modules (e.g. ``test_app.py`` Phase 2.2 cases) start clean.
    """
    if engine_factory is not None:
        app.state.dispatcher = RunDispatcher(
            engine_factory=engine_factory,
            default_budget_s=default_budget_s,
        )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None


def _good_run_body(persona="customer-support", input_text="hello", budget=None):
    spec: dict[str, Any] = {"persona": persona, "input": input_text}
    if budget is not None:
        spec["budget_override"] = {"max_wall_clock_s": budget}
    return {"apiVersion": "corvin/v1", "kind": "Run", "spec": spec}


def _hdr() -> dict[str, str]:
    return {}


def _poll_until_terminal(
    client, url: str, headers: dict[str, str], *,
    timeout_s: float = 5.0,
):
    """Poll GET until status leaves ``accepted`` / ``running`` or timeout."""
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        r = client.get(url, headers=headers)
        last = r
        if r.status_code == 200 and r.json().get("status") in (
            "completed", "failed", "budget_exceeded",
        ):
            return r
        time.sleep(0.02)
    return last


# ── Stub engines for failure / timeout / env-recording tests ─────────


class _StubBase:
    def cancel(self) -> None:
        # No subprocess to kill in the stubs; record the call for any
        # test that wants to assert it fired.
        type(self).cancel_called = True


class _ErrorEventEngine(_StubBase):
    """Yields one error StreamEvent. Moves run to failed."""
    name = "test-error-event"
    capabilities = {"stream_json": True}

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="error", error="simulated engine refusal")


class _RaisingEngine(_StubBase):
    """Raises during iteration. Moves run to failed with exception class."""
    name = "test-raising"
    capabilities = {"stream_json": True}

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="session_started")
        raise RuntimeError("engine blew up mid-stream")


class _SlowEngine(_StubBase):
    """Sleeps past the budget. Moves run to budget_exceeded."""
    name = "test-slow"
    capabilities = {"stream_json": True}
    cancel_called = False

    def __init__(self, sleep_s: float = 1.0) -> None:
        self._sleep_s = sleep_s

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="session_started")
        time.sleep(self._sleep_s)
        yield StreamEvent(type="turn_completed", text="too late")


class _EnvRecordingEngine(_StubBase):
    """Records the env dict it received; yields a benign completion."""
    name = "test-env"
    capabilities = {"stream_json": True}
    last_env: dict[str, str] | None = None
    last_prompt: str | None = None

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        type(self).last_env = dict(env or {})
        type(self).last_prompt = prompt
        yield StreamEvent(
            type="turn_completed", text="ok",
            usage={"input_tokens": 7, "output_tokens": 3},
        )


# ── Tests ────────────────────────────────────────────────────────────


class HappyPathTests(unittest.TestCase):
    def test_post_dispatch_completes_via_fake_engine(self):
        with sandbox(("acme",)) as home:
            with gateway_client() as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(persona="docs", input_text="ping"),
                    headers=_hdr(),
                )
                self.assertEqual(r.status_code, 202)
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
                self.assertIsNotNone(got)
                self.assertEqual(got.status_code, 200, got.text)
                body = got.json()
                self.assertEqual(body["status"], "completed", body)
                self.assertIsNotNone(body["result"])
                self.assertIn("final_text", body["result"])
                # fake-stream emits "[fake-stream] gateway:run_…  :: ping"
                self.assertIn("ping", body["result"]["final_text"])
                self.assertIsNone(body["error"])


class EngineFailureTests(unittest.TestCase):
    def test_error_event_fails_run(self):
        with sandbox(("acme",)) as home:
            with gateway_client(engine_factory=_ErrorEventEngine) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(),
                    headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
                body = got.json()
                self.assertEqual(body["status"], "failed", body)
                self.assertIn("simulated engine refusal", body["error"] or "")

    def test_exception_fails_run(self):
        with sandbox(("acme",)) as home:
            with gateway_client(engine_factory=_RaisingEngine) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(),
                    headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
                body = got.json()
                self.assertEqual(body["status"], "failed", body)
                self.assertIn("RuntimeError", body["error"] or "")
                self.assertIn("engine blew up", body["error"] or "")


class BudgetTimeoutTests(unittest.TestCase):
    def test_slow_engine_hits_budget(self):
        with sandbox(("acme",)) as home:
            with gateway_client(
                engine_factory=lambda: _SlowEngine(sleep_s=2.0),
                default_budget_s=60,  # not used; the run sets explicit budget
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(budget=1),  # 1s wall-clock budget
                    headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    timeout_s=10.0,
                )
                body = got.json()
                self.assertEqual(body["status"], "budget_exceeded", body)
                self.assertIn("wall_clock_timeout", body["error"] or "")


class TenantEnvTests(unittest.TestCase):
    def test_corvin_tenant_id_in_engine_env(self):
        with sandbox(("acme",)) as home:
            # Reset the recording slot
            _EnvRecordingEngine.last_env = None
            _EnvRecordingEngine.last_prompt = None
            with gateway_client(engine_factory=_EnvRecordingEngine) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(persona="research", input_text="x"),
                    headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertIsNotNone(_EnvRecordingEngine.last_env)
            env = _EnvRecordingEngine.last_env
            self.assertEqual(env.get("CORVIN_TENANT_ID"), "acme")
            self.assertEqual(env.get("CORVIN_CALLER_PERSONA"), "research")
            self.assertTrue(
                env.get("CORVIN_CHANNEL_ID", "").startswith("gateway:run_"),
            )
            self.assertEqual(_EnvRecordingEngine.last_prompt, "x")


class CrossTenantIsolationTests(unittest.TestCase):
    def test_two_tenants_records_stay_separate(self):
        # ADR-0149 LIC-GW-CQ-01: the gateway now charges compute_units_per_day at
        # _run_one (a global per-UTC-day counter, like the console/ACS gates). This
        # test fires TWO runs in one corvin_home to prove record isolation, so give
        # it an unlimited license — the quota axis is not what is under test here.
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator"))
        import license.validator as _v
        _orig_lic, _orig_can = _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY
        _v._set_active_license({"tier": "enterprise", "limits": {"compute_units_per_day": None}})
        self.addCleanup(lambda: setattr(_v, "_ACTIVE_LICENSE_CANARY", _orig_can))
        self.addCleanup(lambda: setattr(_v, "_ACTIVE_LICENSE", _orig_lic))
        with sandbox(("acme", "globex")) as home:
            with gateway_client() as client:
                r_a = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(input_text="for-acme"),
                    headers=_hdr(),
                )
                r_g = client.post(
                    "/v1/tenants/globex/runs",
                    json=_good_run_body(input_text="for-globex"),
                    headers=_hdr(),
                )
                rid_a = r_a.json()["run_id"]
                rid_g = r_g.json()["run_id"]
                got_a = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{rid_a}", _hdr(),
                )
                got_g = _poll_until_terminal(
                    client, f"/v1/tenants/globex/runs/{rid_g}", _hdr(),
                )
                self.assertEqual(got_a.json()["status"], "completed")
                self.assertEqual(got_g.json()["status"], "completed")
                # Records reference the right tenant
                self.assertEqual(got_a.json()["tenant_id"], "acme")
                self.assertEqual(got_g.json()["tenant_id"], "globex")
                # Cross-tenant URL: acme's run_id is not in globex's namespace →
                # 404 (token-based cross-tenant 403 was removed with atlr_* auth;
                # OIDC cross-tenant enforcement is deferred to cloud deployment).
                cross = client.get(
                    f"/v1/tenants/globex/runs/{rid_a}", headers=_hdr(),
                )
                self.assertIn(cross.status_code, (403, 404))


class GatewayComputeQuotaTests(unittest.TestCase):
    """ADR-0149 LIC-GW-CQ-01: gateway run-dispatch charges compute_units_per_day."""

    def test_free_tier_second_run_blocked(self):
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator"))
        import license.validator as _v
        _orig, _orig_can = _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY
        _v._set_active_license(None)  # free tier: compute_units_per_day = 1
        self.addCleanup(lambda: setattr(_v, "_ACTIVE_LICENSE_CANARY", _orig_can))
        self.addCleanup(lambda: setattr(_v, "_ACTIVE_LICENSE", _orig))
        with sandbox(("acme",)) as home:
            with gateway_client() as client:
                r1 = client.post("/v1/tenants/acme/runs",
                                 json=_good_run_body(input_text="one"), headers=_hdr())
                g1 = _poll_until_terminal(client, f"/v1/tenants/acme/runs/{r1.json()['run_id']}", _hdr())
                self.assertEqual(g1.json()["status"], "completed")
                r2 = client.post("/v1/tenants/acme/runs",
                                 json=_good_run_body(input_text="two"), headers=_hdr())
                g2 = _poll_until_terminal(client, f"/v1/tenants/acme/runs/{r2.json()['run_id']}", _hdr())
                self.assertEqual(g2.json()["status"], "failed",
                                 "2nd free-tier gateway run must be blocked by compute_units_per_day")
                self.assertIn("compute_units_per_day", g2.json().get("error", ""))


class DrainTests(unittest.TestCase):
    def test_lifespan_drains_in_flight_on_exit(self):
        with sandbox(("acme",)) as home:
            with gateway_client() as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(),
                    headers=_hdr(),
                )
                self.assertEqual(r.status_code, 202)
                # Don't poll — let lifespan exit drain the work.
                # After context exit the run MUST be in a terminal state.
                run_id = r.json()["run_id"]
            # Context has exited; read the on-disk record directly
            # (the lifespan drain awaited the dispatcher).
            from corvin_gateway.runs import RunRegistry
            record = RunRegistry().get("acme", run_id)
            self.assertIn(record.status, ("completed", "failed", "budget_exceeded"))


# ── Regression: CancelledError must not strand the run / orphan the engine ──


class _BlockingEngine(_StubBase):
    """Blocks inside spawn() until released, so the dispatch task can be
    cancelled mid-flight (models drain/shutdown cancelling an in-flight run)."""
    name = "test-blocking"
    capabilities = {"stream_json": True}
    cancel_called = False
    spawned = threading.Event()
    release = threading.Event()

    def cancel(self) -> None:
        # Record the call AND unblock the worker thread so the test exits
        # promptly (the real engine.cancel() kills the claude -p subprocess).
        type(self).cancel_called = True
        type(self).release.set()

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        type(self).spawned.set()
        type(self).release.wait(timeout=30)
        yield StreamEvent(type="turn_completed", text="late")


class CancelledErrorTests(unittest.TestCase):
    """Fix #1 — the CancelledError handler must engine.cancel() + move the run
    to a terminal state before re-raising, or the run is stranded at
    status='running' forever and the engine subprocess leaks."""

    def test_cancel_sets_terminal_and_cancels_engine(self):
        _BlockingEngine.cancel_called = False
        _BlockingEngine.spawned.clear()
        _BlockingEngine.release.clear()

        async def _drive():
            from corvin_gateway.dispatcher import RunDispatcher
            from corvin_gateway.runs import RunRequest
            disp = RunDispatcher(engine_factory=_BlockingEngine,
                                 default_budget_s=60)
            req = RunRequest.model_validate(
                _good_run_body(persona="docs", input_text="hello"))
            rec = disp._registry.create("acme", req)
            task = asyncio.create_task(disp._run_one("acme", rec.run_id))
            # Wait until the engine is spawned (task is inside wait_for()).
            for _ in range(300):
                if _BlockingEngine.spawned.is_set():
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(_BlockingEngine.spawned.is_set(),
                            "engine never spawned — cannot exercise cancel path")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return disp, rec.run_id

        with sandbox(("acme",)):
            disp, run_id = asyncio.run(_drive())
            # Engine was told to abandon work (no orphaned subprocess).
            self.assertTrue(_BlockingEngine.cancel_called,
                            "CancelledError path must call engine.cancel()")
            # Run reached a terminal state (not stranded at 'running').
            record = disp._registry.get("acme", run_id)
            self.assertIn(record.status, ("completed", "failed", "budget_exceeded"))
            self.assertEqual(record.status, "failed", record.status)
            self.assertIn("cancelled", (record.error or "").lower())


# ── Regression: gateway EXECUTE path must enforce L34 (data-classification) ──


class _BenignEngine(_StubBase):
    """Completes immediately — used to prove a gate BLOCKED the spawn (if the
    gate did not fire, the run would reach 'completed')."""
    name = "test-benign"
    capabilities = {"stream_json": True}

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="turn_completed", text="ok")


class GatewayL34GateTests(unittest.TestCase):
    """Fix #2 — the gateway spawn path (_run_one) must invoke the shared
    spawn_gates.check_l34 and fail-closed on its refusal, exactly like the
    console / adapter / a2a_worker do. A CONFIDENTIAL-classified run must be
    denied before any spawn."""

    def test_l34_refusal_denies_run(self):
        import corvin_gateway.dispatcher as _disp
        calls: dict[str, Any] = {}

        def _spy_l34(engine_id, tenant_id, *, prompt=None, persona=None,
                     channel="", chat_key="", **kw):
            calls["engine_id"] = engine_id
            calls["prompt"] = prompt
            calls["channel"] = channel
            return ("[data-flow] Spawn rejected: Classification CONFIDENTIAL is "
                    "not allowed with engine 'test-benign'.")

        orig_l34 = _disp._check_l34
        _disp._check_l34 = _spy_l34

        async def _drive():
            disp = _disp.RunDispatcher(engine_factory=_BenignEngine,
                                       default_budget_s=60)
            from corvin_gateway.runs import RunRequest
            req = RunRequest.model_validate(
                _good_run_body(persona="docs", input_text="secret dossier"))
            rec = disp._registry.create("acme", req)
            await disp._run_one("acme", rec.run_id)
            return disp, rec.run_id

        try:
            # NOTE: assert INSIDE the sandbox — sandbox() pops CORVIN_HOME on
            # exit, so registry.get() must run while it is still set.
            with sandbox(("acme",)):
                disp, run_id = asyncio.run(_drive())
                # The gate was invoked on the gateway path with the run's prompt.
                self.assertEqual(calls.get("prompt"), "secret dossier")
                self.assertEqual(calls.get("channel"), "gateway")
                # Fail-closed: the run was denied (never reached the engine).
                record = disp._registry.get("acme", run_id)
                self.assertEqual(record.status, "failed", record.status)
                self.assertIn("data-flow", record.error or "")
        finally:
            _disp._check_l34 = orig_l34

    def test_l34_none_allows_run_through(self):
        """Control: when check_l34 returns None (pass/fail-open) the run is not
        blocked by L34 — proving the gate is a real gate, not a hard-deny."""
        import corvin_gateway.dispatcher as _disp
        orig_l34 = _disp._check_l34
        _disp._check_l34 = lambda *a, **k: None

        async def _drive():
            from corvin_gateway.runs import RunRequest
            disp = _disp.RunDispatcher(engine_factory=_BenignEngine,
                                       default_budget_s=60)
            req = RunRequest.model_validate(
                _good_run_body(persona="docs", input_text="hello"))
            rec = disp._registry.create("acme", req)
            await disp._run_one("acme", rec.run_id)
            return disp, rec.run_id

        # Give this control an unlimited license so compute-quota (a DIFFERENT
        # gate) doesn't fail the run and mask the L34 result.
        _s = sys
        _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator"))
        import license.validator as _v
        _orig, _orig_can = _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY
        _v._set_active_license({"tier": "enterprise",
                                "limits": {"compute_units_per_day": None}})
        try:
            with sandbox(("acme",)):
                disp, run_id = asyncio.run(_drive())
                record = disp._registry.get("acme", run_id)
                # L34 did not block; the run completed via the benign engine.
                self.assertEqual(record.status, "completed", record.error)
        finally:
            _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY = _orig, _orig_can
            _disp._check_l34 = orig_l34


if __name__ == "__main__":
    unittest.main(verbosity=2)
