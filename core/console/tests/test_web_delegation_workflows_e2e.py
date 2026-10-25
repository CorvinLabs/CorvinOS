"""E2E — ADR-0114 / PLAN-0961: fictional delegated workflows over the chat WebSocket.

Fixtures (``fixtures/delegation_workflows/*.json``) describe six fictional
workflows, each driven by a deterministic stub engine instead of a real model:

  (a) simple_fan_out_fan_in  — 3 parallel workers, one synthesis
  (b) complex_dag            — 3 stages, parallel branches, fan-in nodes,
                               dependencies between stages
  (c) partial_failure        — one worker raises; the run must say so
  (d) artifact_output        — a worker writes a deliverable file
  (e) single_worker          — the simplest form: one worker, no fan-out
  (f) abort_mid_fan_out      — the user cancels while stage 1 is running

plus a budget stop driven by the operator's delegation budget (ADR-0195:
a reported stop, not an error) on fixture (a).

Transport: the real ``/v1/console/chat/sessions/{sid}/stream`` WebSocket
(``chat.chat_stream``) drives the real ``chat_runtime.stream_turn`` and the
real ``ACSRuntime`` (manager loop, ``_dispatch_workers``, worker span
emission, audit chain). Only the model-call boundary is faked
(``_call_manager_sync`` / ``_call_worker_sync``), plus house-rules, spawn
gates, licence quota and engine resolution — the same boundary the M2.1
artifact suite fakes. Calling ``stream_turn`` directly does NOT count here.

Parallelism is measured, not assumed: every worker of a stage waits on a
``threading.Barrier`` of the stage's size before it returns. A serial
dispatch breaks the barrier and the worker fails.

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME in a fresh temp dir,
CORVIN_AUDIT_ANCHOR_KEY absolute inside it, FORGE_ROOT unset. The audit chain
read back is the tenant chain under that sandbox, never the live install.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / _p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_FIXTURES = _THIS.parent / "fixtures" / "delegation_workflows"
_SUBTASK_RE = re.compile(r"SUBTASK ID: (\S+)")


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "test-fp"
    csrf_token = "t"


class _FixtureEngine:
    """Deterministic manager + worker engine driven by one workflow fixture."""

    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture
        self.manager_prompts: list[str] = []
        self.worker_prompts: dict[str, str] = {}
        self.worker_log: list[tuple[str, str, float]] = []  # (kind, subtask, monotonic)
        self._lock = threading.Lock()
        self._manager_idx = 0
        stage_sizes: dict[int, int] = {}
        for cfg in fixture["workers"].values():
            stage_sizes[cfg["stage"]] = stage_sizes.get(cfg["stage"], 0) + 1
        self._barriers = {s: threading.Barrier(n, timeout=5) for s, n in stage_sizes.items()}
        # Released by the test (or its cleanup) for workers with behavior=block.
        self.release = threading.Event()

    def manager(self, prompt, model, tenant_id="_default", proc_holder=None):
        with self._lock:
            self.manager_prompts.append(prompt)
            decision = self.fixture["manager"][min(self._manager_idx,
                                                   len(self.fixture["manager"]) - 1)]
            self._manager_idx += 1
        return json.dumps(decision), 100

    def worker(self, prompt, system, model, budget, extra_env=None,
               tenant_id="_default", proc_holder=None):
        sid = _SUBTASK_RE.search(prompt).group(1)
        cfg = self.fixture["workers"][sid]
        with self._lock:
            self.worker_prompts[sid] = prompt
            self.worker_log.append(("start", sid, _now()))
        # All workers of this stage must be alive at the same time.
        self._barriers[cfg["stage"]].wait()
        with self._lock:
            self.worker_log.append(("end", sid, _now()))
        if cfg.get("behavior") == "block":
            # Held open until the test releases it: the run is cancelled meanwhile.
            self.release.wait(timeout=15)
        if cfg.get("behavior") == "raise":
            raise RuntimeError(cfg["error"])
        if cfg.get("artifact"):
            run_dirs = sorted(self._runs_root.iterdir())
            out = run_dirs[-1] / "output"
            out.mkdir(parents=True, exist_ok=True)
            (out / cfg["artifact"]["name"]).write_text(cfg["artifact"]["text"], encoding="utf-8")
        tok = cfg["tokens"]
        attestation = {
            "engine_id": "claude_code",
            "model_id": model,
            "attested": bool(cfg["reported_model"]),
            "reported_model": cfg["reported_model"],
            "locality": "us_cloud",
            "input_tokens": tok["input_tokens"],
            "output_tokens": tok["output_tokens"],
            "cache_read_input_tokens": tok["cache_read_input_tokens"],
            "cache_creation_input_tokens": tok["cache_creation_input_tokens"],
        }
        return json.dumps(cfg["output"]), 50, attestation


def _now() -> float:
    import time
    return time.monotonic()


class DelegatedWorkflowE2EBase(unittest.TestCase):
    fixture_name = ""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in (
            "CORVIN_HOME", "XDG_CONFIG_HOME", "HOME", "CORVIN_TENANT_ID",
            "CORVIN_AUDIT_ANCHOR_KEY", "ANTHROPIC_API_KEY", "FORGE_ROOT",
            "CORVIN_WDAT_KEY")}
        for k in self._saved:
            self.addCleanup(self._restore_env, k)
        os.environ["CORVIN_HOME"] = str(root / "corvin")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["HOME"] = str(root / "home")
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(root / "anchor.key")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-sandbox-dummy"
        os.environ.pop("FORGE_ROOT", None)
        os.environ.pop("CORVIN_WDAT_KEY", None)

        from corvin_console import chat_runtime
        from corvin_console.routes import chat as chat_routes
        self.cr = chat_runtime
        self.routes = chat_routes
        self.fixture = _load(self.fixture_name)
        self.engine = _FixtureEngine(self.fixture)
        self.sess = chat_runtime.create_session("_default")
        self.engine._runs_root = self.sess.workdir / "acs" / "runs"

    def _restore_env(self, key: str) -> None:
        prev = self._saved[key]
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    # ── boundary fakes (same set as the M2.1 artifact suite) ───────────────

    def _patches(self):
        import house_rules as _hr  # type: ignore
        import spawn_gates as _sg  # type: ignore
        import acs_runtime as _rt  # type: ignore

        orig = getattr(_hr, "_house_rules_classifier", None)
        self.addCleanup(setattr, _hr, "_house_rules_classifier", orig)
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear"))

        limits_mod = types.ModuleType("license.limits")

        class _E(Exception):
            pass

        limits_mod.LicenseLimitError = _E  # type: ignore[attr-defined]
        quota_mod = types.ModuleType("license.compute_quota")
        quota_mod.increment_and_check = (  # type: ignore[attr-defined]
            lambda home, channel=None, chat_key=None: None)
        saved_mods = {m: sys.modules.get(m) for m in
                      ("license", "license.compute_quota", "license.limits")}
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

        def _restore_mods():
            for m, prev in saved_mods.items():
                if prev is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = prev
        self.addCleanup(_restore_mods)

        async def _no_spawn(*args, **kwargs):
            proc = AsyncMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=0)
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            proc.stdout.read = AsyncMock(return_value=b"")
            proc.stderr = AsyncMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            proc.stdin = MagicMock()
            proc.stdin.drain = AsyncMock()
            return proc

        rec = _Rec()
        return [
            patch.object(asyncio, "create_subprocess_exec", side_effect=_no_spawn),
            # Opt-in: the tenant has delegation switched on (ADR-0114).
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"),
            patch.object(self.cr, "_engine_unavailable_message", return_value=None),
            patch.object(_rt, "_call_manager_sync", side_effect=self.engine.manager),
            patch.object(_rt, "_call_worker_sync", side_effect=self.engine.worker),
            patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "test-model")),
            patch.object(_sg, "check_l44", return_value=None),
            patch.object(_sg, "check_l34", return_value=None),
            patch.object(_sg, "check_l35", return_value=None),
            patch.object(self.routes.session_auth, "load_session", return_value=rec),
        ]

    # ── transport ─────────────────────────────────────────────────────────

    def _run_over_ws(self) -> list[dict]:
        """One user turn over the real WebSocket; returns every server event."""
        app = FastAPI()
        app.include_router(self.routes.router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", "valid-sid")
        for p in self._patches():
            p.start()
            self.addCleanup(p.stop)
        events: list[dict] = []
        with client.websocket_connect(
                f"/v1/console/chat/sessions/{self.sess.sid}/stream") as ws:
            events.append(ws.receive_json())
            self.assertEqual(events[0]["type"], "ready")
            ws.send_json({"type": "user", "text": self.fixture["prompt"]})
            while True:
                ev = ws.receive_json()
                events.append(ev)
                if ev.get("type") in ("done", "error"):
                    break
        self.events = events
        return events

    # ── audit chain ───────────────────────────────────────────────────────

    def _chain_records(self) -> list[dict]:
        from forge import security_events as _sec  # type: ignore
        from forge.paths import tenant_audit_chain  # type: ignore

        path = tenant_audit_chain("_default")
        ok, problems = _sec.verify_chain(path)
        self.assertTrue(ok, f"audit hash chain broken: {problems[:3]}")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _worker_spans(self, records: list[dict], kind: str) -> list[dict]:
        return [r for r in records if r.get("event_type") == f"engine.span.{kind}"
                and (r.get("details") or {}).get("role") == "worker"]

    # ── shared assertions ─────────────────────────────────────────────────

    def _assert_stream_shape(self, events: list[dict]) -> None:
        types_ = [e.get("type") for e in events]
        self.assertEqual(types_[0], "ready")
        self.assertEqual(types_[-1], "done", types_)
        self.assertNotIn("error", types_, events)
        self.assertIn("result", types_, events)
        self.assertLess(types_.index("result"), types_.index("done"))
        deltas = "".join(e.get("text") or "" for e in events if e.get("type") == "delta")
        self.assertIn("Delegated to ACS workers", deltas, "prompt must take the ACS fan-out")

    def _assert_result_text(self, events: list[dict]) -> str:
        result = next(e for e in events if e.get("type") == "result")
        text = result.get("text") or ""
        for needle in self.fixture["expect"]["result_contains"]:
            self.assertIn(needle, text, f"synthesis missing {needle!r}: {text!r}")
        return text

    def _assert_worker_spans_priced(self, records: list[dict]) -> None:
        self._assert_tenant_tagged(records)
        starts = self._worker_spans(records, "start")
        ends = self._worker_spans(records, "end")
        expected = self.fixture["workers"]
        self.assertEqual(len(starts), len(expected), "one worker span start per worker")
        self.assertEqual(len(ends), len(expected), "one worker span end per worker")
        by_span_ends = {}
        for e in ends:
            d = e["details"]
            sid = d["span_id"].rsplit("-", 1)[-1]
            by_span_ends[sid] = d
        for sid, cfg in expected.items():
            self.assertIn(sid, by_span_ends, f"no span end for worker {sid}")
            d = by_span_ends[sid]
            if cfg.get("behavior") == "raise":
                self.assertEqual(d.get("status"), "error", f"failed worker {sid} span: {d}")
                continue
            self.assertEqual(d.get("status"), "ok", d)
            self.assertEqual(d.get("model_id"), cfg["reported_model"],
                             f"span {sid} must carry the engine-reported model")
            tok = cfg["tokens"]
            self.assertEqual(d.get("input_tokens"), tok["input_tokens"])
            self.assertEqual(d.get("output_tokens"), tok["output_tokens"])
            self.assertEqual(d.get("cache_read_tokens"), tok["cache_read_input_tokens"])
            self.assertEqual(d.get("cache_write_tokens"), tok["cache_creation_input_tokens"])

    def _assert_tenant_tagged(self, records: list[dict]) -> None:
        """Every ACS record and every worker/manager span of this run names its tenant.

        The OS-turn spans (role=os) are written by the shared bridge audit writer,
        not by ACSRuntime, and stay untagged; that gap is outside this suite and is
        reported, not asserted here.
        """
        run_records = [r for r in records
                       if str(r.get("event_type", "")).startswith("acs.")
                       or ((str(r.get("event_type", "")).startswith("engine.span.")
                            and (r.get("details") or {}).get("role") in ("worker", "manager")))]
        self.assertTrue(run_records, "no ACS records in the tenant chain")
        untagged = [(r["event_type"], (r.get("details") or {}).get("role"))
                    for r in run_records
                    if (r.get("details") or {}).get("tenant_id") != "_default"]
        self.assertEqual(untagged, [], "ACS/span records without tenant_id")

    def _assert_stages_ran_in_order(self) -> None:
        """Stage k+1 starts only after every worker of stage k has finished."""
        stage_of = {sid: cfg["stage"] for sid, cfg in self.fixture["workers"].items()}
        end_at = {sid: t for kind, sid, t in self.engine.worker_log if kind == "end"}
        start_at = {sid: t for kind, sid, t in self.engine.worker_log if kind == "start"}
        for sid, stage in stage_of.items():
            for other, other_stage in stage_of.items():
                if other_stage < stage:
                    self.assertLess(end_at[other], start_at[sid],
                                    f"{sid} (stage {stage}) started before {other} "
                                    f"(stage {other_stage}) finished")

    def _assert_fan_in_context(self) -> None:
        for sid, markers in self.fixture["expect"].get("fan_in_context", {}).items():
            for marker in markers:
                self.assertIn(marker, self.engine.worker_prompts[sid],
                              f"fan-in node {sid} did not receive upstream {marker}")


class SimpleFanOutFanInE2E(DelegatedWorkflowE2EBase):
    fixture_name = "simple_fan_out_fan_in"

    def test_fan_out_fan_in_over_websocket(self) -> None:
        events = self._run_over_ws()
        self._assert_stream_shape(events)
        self._assert_result_text(events)
        self.assertEqual(len(self.engine.worker_prompts), self.fixture["expect"]["worker_count"])
        records = self._chain_records()
        self._assert_worker_spans_priced(records)
        self._assert_tenant_tagged(records)

    def test_workers_ran_in_parallel(self) -> None:
        self._run_over_ws()
        # A serial dispatch would have broken the stage barrier and failed a worker.
        self.assertEqual(
            sorted(sid for kind, sid, _ in self.engine.worker_log if kind == "end"),
            sorted(self.fixture["workers"]))
        records = self._chain_records()
        starts = [r["ts"] for r in self._worker_spans(records, "start")]
        ends = [r["ts"] for r in self._worker_spans(records, "end")]
        self.assertLess(max(starts), min(ends),
                        "worker spans must overlap in time (parallel fan-out)")


class ComplexDagE2E(DelegatedWorkflowE2EBase):
    fixture_name = "complex_dag"

    def test_multi_stage_dag_over_websocket(self) -> None:
        events = self._run_over_ws()
        self._assert_stream_shape(events)
        self._assert_result_text(events)
        self.assertEqual(len(self.engine.worker_prompts), self.fixture["expect"]["worker_count"])
        self.assertEqual(len(self.engine.manager_prompts), 4, "3 DELEGATE stages + 1 COMPLETE")
        self._assert_stages_ran_in_order()
        self._assert_fan_in_context()
        self._assert_worker_spans_priced(self._chain_records())

    def test_stage_branches_ran_in_parallel(self) -> None:
        self._run_over_ws()
        # Barriers per stage were all satisfied: stage 1 (3 workers) and
        # stage 2 (2 workers) each had every member alive at once.
        self.assertEqual(
            sorted(sid for kind, sid, _ in self.engine.worker_log if kind == "end"),
            sorted(self.fixture["workers"]))
        records = self._chain_records()
        spans = self._worker_spans(records, "start")
        self.assertEqual(len(spans), 6)


class PartialFailureE2E(DelegatedWorkflowE2EBase):
    fixture_name = "partial_failure"

    def test_failed_worker_is_reported_not_silently_dropped(self) -> None:
        events = self._run_over_ws()
        # The run completes with a result; the failure is named in it.
        self._assert_stream_shape(events)
        self._assert_result_text(events)
        records = self._chain_records()
        self._assert_worker_spans_priced(records)
        worker_errors = [r for r in records if r.get("event_type") == "acs.worker_error"]
        self.assertEqual([r["details"]["worker_id"] for r in worker_errors], ["w2"])
        # The manager saw the failure in its next decision input.
        self.assertIn("[w2] status=failed", self.engine.manager_prompts[1])

    def test_healthy_workers_still_deliver_their_results(self) -> None:
        self._run_over_ws()
        self.assertIn("QUELLE_ALPHA", self.engine.manager_prompts[1])
        self.assertIn("QUELLE_GAMMA", self.engine.manager_prompts[1])


class ArtifactOutputE2E(DelegatedWorkflowE2EBase):
    fixture_name = "artifact_output"

    def test_worker_deliverable_streams_before_done(self) -> None:
        events = self._run_over_ws()
        self._assert_stream_shape(events)
        self._assert_result_text(events)
        types_ = [e.get("type") for e in events]
        artifacts = [e for e in events if e.get("type") == "artifact"]
        names = [a.get("name") for a in artifacts]
        self.assertIn("marktbericht.md", names, events)
        # ACS also attaches its own delegation graph; that is the only extra chip.
        self.assertEqual([n for n in names if n != "marktbericht.md"],
                         ["acs_delegation_graph.png"], events)
        self.assertLess(max(i for i, t in enumerate(types_) if t == "artifact"),
                        types_.index("done"))
        run_dirs = sorted((self.sess.workdir / "acs" / "runs").iterdir())
        self.assertTrue((run_dirs[-1] / "output" / "marktbericht.md").is_file())
        self._assert_worker_spans_priced(self._chain_records())


class SingleWorkerE2E(DelegatedWorkflowE2EBase):
    """The simplest form (T-0234): one subtask, one worker, no fan-out."""

    fixture_name = "single_worker"

    def test_single_worker_over_websocket(self) -> None:
        events = self._run_over_ws()
        self._assert_stream_shape(events)
        self._assert_result_text(events)
        self.assertEqual(len(self.engine.worker_prompts), self.fixture["expect"]["worker_count"])
        self.assertEqual(len(self.engine.manager_prompts), 2, "1 DELEGATE + 1 COMPLETE")
        self._assert_worker_spans_priced(self._chain_records())


class BudgetStopE2E(DelegatedWorkflowE2EBase):
    """Operator budget reached (ADR-0195, T-0235): a reported stop, never an error.

    The operator caps the delegation at three workers through the same file the
    console Settings UI writes. Round 1 fans out to exactly three workers; the
    budget check before round 2 stops the run.

    Not ``max_loops=1``: the dispatch re-checks the root budget AFTER the loop
    counter was incremented for the current round, so a one-round budget refuses
    the round's own workers (``acs.budget_breach``) and nothing runs. That is a
    separate finding, recorded in the task, not asserted here.
    """

    fixture_name = "simple_fan_out_fan_in"

    def setUp(self) -> None:
        super().setUp()
        gdir = self.cr._forge_paths.tenant_global_dir("_default")
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "delegation_budget.json").write_text(
            json.dumps({"max_total_workers": 3}), encoding="utf-8")

    def test_budget_stop_is_reported_not_an_error(self) -> None:
        events = self._run_over_ws()
        types_ = [e.get("type") for e in events]
        self.assertEqual(types_[-1], "done", types_)
        self.assertNotIn("error", types_, events)
        result = next(e for e in events if e.get("type") == "result")
        text = result.get("text") or ""
        self.assertIn("Budget erreicht", text, text)
        self.assertIn("kein Fehler", text, text)
        self.assertNotIn("fehlgeschlagen", text.lower(), text)

    def test_budget_stop_is_audited_after_the_first_round_ran(self) -> None:
        self._run_over_ws()
        # The first round ran in full before the stop.
        self.assertEqual(len(self.engine.worker_prompts), 3)
        self.assertEqual(len(self.engine.manager_prompts), 1, "no second planning round")
        records = self._chain_records()
        stops = [r for r in records if r.get("event_type") == "acs.budget_exhausted"]
        self.assertEqual(len(stops), 1, "exactly one acs.budget_exhausted record")
        self.assertIn("max_total_workers=3", stops[0]["details"].get("reason", ""))
        self._assert_worker_spans_priced(records)


class AbortMidFanOutE2E(DelegatedWorkflowE2EBase):
    """The user cancels while stage 1 is running (T-0235)."""

    fixture_name = "abort_mid_fan_out"

    def _run_and_cancel(self) -> list[dict]:
        import time

        app = FastAPI()
        app.include_router(self.routes.router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", "valid-sid")
        for p in self._patches():
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.engine.release.set)
        events: list[dict] = []
        started: set[str] = set()
        with client.websocket_connect(
                f"/v1/console/chat/sessions/{self.sess.sid}/stream") as ws:
            events.append(ws.receive_json())
            self.assertEqual(events[0]["type"], "ready")
            ws.send_json({"type": "user", "text": self.fixture["prompt"]})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with self.engine._lock:
                    started = {sid for kind, sid, _ in self.engine.worker_log if kind == "start"}
                if len(started) == 3:
                    break
                time.sleep(0.05)
            self.assertEqual(len(started), 3, "stage 1 did not fan out before the cancel")
            ws.send_json({"type": "cancel"})
            while True:
                ev = ws.receive_json()
                events.append(ev)
                if ev.get("type") in ("done", "error"):
                    break
        self.engine.release.set()
        self.events = events
        return events

    def test_cancel_closes_the_turn_without_a_result(self) -> None:
        events = self._run_and_cancel()
        types_ = [e.get("type") for e in events]
        self.assertEqual(types_[-1], "done", types_)
        self.assertNotIn("error", types_, events)
        self.assertNotIn("result", types_, "a cancelled run must not deliver a synthesis")

    def test_cancel_stops_the_fan_out_and_pairs_every_worker_span(self) -> None:
        self._run_and_cancel()
        # Stage 2 never started and no second planning round happened.
        for sid in self.fixture["expect"]["never_started"]:
            self.assertNotIn(sid, self.engine.worker_prompts, f"{sid} ran after the cancel")
        self.assertEqual(len(self.engine.manager_prompts), 1)
        records = self._chain_records()
        starts = self._worker_spans(records, "start")
        ends = self._worker_spans(records, "end")
        self.assertEqual(len(starts), 3)
        self.assertEqual(len(ends), 3, "every started worker span is closed")
        self.assertEqual({e["details"].get("status") for e in ends}, {"error"})
        self.assertTrue(all(not e["details"].get("model_id") for e in ends),
                        "a cancelled worker reported nothing and stays unpriced")
        traced = [r for r in records if r.get("event_type") == "acs.worker_traced"]
        self.assertEqual(len(traced), 3)
        self.assertEqual({r["details"].get("status") for r in traced}, {"failed"})


if __name__ == "__main__":
    unittest.main()
