"""HTTP E2E proof for the Video Producer console routes (adversarial review 2026-10-03).

Driven through the REAL console router (TestClient, real session cookie + CSRF,
two tenants, a temp CORVIN_HOME). Real: route handlers, the marketplace plugin
storage, the L44/L34/L35 pre-spawn gates, the learning EventStore and its
audit-chain write. Replaced: only the production runner (a real job calls an
LLM, a TTS provider and ffmpeg — external egress a test must not cause); the
recorder below captures exactly what the route hands to it.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox as _base_sandbox  # noqa: E402

_MOD = "corvin_console.routes.video_producer_api"
_INDEX_ID = "plugin:contributor-media-video_producer"


def _install_plugin(tenant: str, *, enable: bool) -> None:
    """Install (and optionally enable) the Video Producer in ``tenant``'s registry through
    the REAL lifecycle — the same record the marketplace install route writes. The API
    is gated on exactly this state, so a test that drives it must have it."""
    from corvin_console.routes import marketplace_resolve as _resolve
    from corvin_plugins.state import PluginLifecycle

    plugin_dir, manifest = _resolve.load_manifest(_INDEX_ID)
    record = _resolve.record_from_manifest(manifest, plugin_dir=plugin_dir)
    lifecycle = PluginLifecycle(tenant_id=tenant, lifecycle_enabled=True)
    lifecycle.install(record, installed_by="test")
    if enable:
        lifecycle.enable(record.plugin_id, consent_granted_by="test")


@contextmanager
def _sandbox(tmp_path, *, tenants=("_default",), plugin: str = "enabled", keep_key: bool = True):
    """``test_admin_route._sandbox`` plus the plugin state the API is gated on
    (``plugin`` = "enabled" | "installed" | "absent")."""
    from unittest import mock

    # The default narration engine is OpenAI: the route refuses a job without a key in the
    # process environment, so every sandbox carries a dummy one (never used: the runner is a recorder).
    env = {"CORVIN_TTS_OPENAI_KEY": "sk-test-dummy-0000000000"} if keep_key else {}
    with mock.patch.dict(os.environ, env), _base_sandbox(tmp_path, tenants=tenants) as boxed:
        if not keep_key:
            os.environ.pop("CORVIN_TTS_OPENAI_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
        if plugin != "absent":
            for tenant in tenants:
                _install_plugin(tenant, enable=plugin == "enabled")
        # A recorder runner never finishes a job, so the per-tenant in-flight cap (2) would refuse the
        # third job of any test; the cap itself is proven in test_video_producer_review_2026_10_10_e2e.
        # The execution watcher polls fast so a test that "finishes" a job sees its event within moments.
        mod = sys.modules.get(_MOD)
        with contextlib.ExitStack() as stack:
            if mod is not None:
                if hasattr(mod, "_MAX_ACTIVE_JOBS_PER_TENANT"):
                    stack.enter_context(mock.patch.object(mod, "_MAX_ACTIVE_JOBS_PER_TENANT", 1000))
                if hasattr(mod, "_EXEC_POLL_S"):
                    stack.enter_context(mock.patch.object(mod, "_EXEC_POLL_S", 0.05))
            yield boxed


class _RecordingRunner:
    def __init__(self, fail: bool = False):
        self.calls: list = []
        self.fail = fail

    async def start_job(self, job_id, task, config):
        if self.fail:
            raise RuntimeError("secret internal detail /home/x")
        self.calls.append((job_id, task, config))
        return job_id


def _route_module():
    return sys.modules[_MOD]


def _finish_job(home: Path, tenant: str, job_id: str, status: str = "complete", timeout: float = 10.0) -> None:
    """Do what a runner does when a job ends - persist the end state through the plugin's own storage -
    then wait until the host's watcher has recorded the execution (the ADR-0534 reality check reads it)."""
    import time
    from datetime import datetime, timedelta

    from core.learning.learning_events import EventType
    from core.paths.tenant import tenant_home

    mod = _route_module()
    storage = mod._get_storage(str(home / "tenants" / tenant / "video_producer"))
    job = storage.get_job(job_id)
    job.status, job.started_at = status, datetime.now() - timedelta(seconds=3)
    job.completed_at = datetime.now()
    storage.save_job(job)
    from core.learning.event_store import EventStore

    store = EventStore(tenant_home(tenant), tenant_id=tenant)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if any((e.signal or {}).get("task_id") == job_id
               for e in store.query_events(tenant, event_type=EventType.SKILL_EXECUTED, skill_id="os.video_producer")):
            return
        time.sleep(0.05)
    raise AssertionError(f"no skill_executed event for {job_id} within {timeout}s")


def _chain_text(home: Path) -> str:
    return "\n".join(f.read_text() for f in home.rglob("audit.jsonl"))


def _write_tenant_yaml(home: Path, tenant: str, spec: dict) -> None:
    path = home / "tenants" / tenant / "global" / "tenant.corvin.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump({"spec": spec}, f)


class VideoProducerRoutesE2E(unittest.TestCase):
    """The L44 Tier-1 classifier leaf is stubbed to a benign verdict (the repo's
    convention, see test_console_spawn_gates.py) so no test spawns a real
    ``claude -p``; the gate plumbing around it runs for real."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._restore: list = []

    def tearDown(self):
        for obj, attr, orig in reversed(self._restore):
            setattr(obj, attr, orig)

    def _benign_l44(self):
        import house_rules as _hr  # type: ignore  # on sys.path via corvin_console._spawn_gates
        self._restore.append((_hr, "_house_rules_classifier", _hr._house_rules_classifier))
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 0.99, "benign (test stub)")

    def _create(self, client, csrf, task="Explain the CorvinOS audit chain in three short scenes."):
        self._benign_l44()
        return client.post("/v1/console/video/jobs", json={"task": task}, headers={"X-CSRF-Token": csrf})

    # ── A1 / A13 ────────────────────────────────────────────────────────────
    def test_jobs_are_tenant_isolated_and_no_bare_module_names(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            self.assertIsNotNone(mod.VideoJob, "plugin did not load from the sibling marketplace")
            for bare in ("models", "storage", "skill"):
                m = sys.modules.get(bare)
                self.assertFalse(
                    m is not None and "video_producer" in str(getattr(m, "__file__", "")),
                    f"route registered the plugin as bare module {bare!r}",
                )
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            (ca, csrf_a), (cb, _csrf_b) = clients["tenant_a"], clients["tenant_b"]

            r = self._create(ca, csrf_a)
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["job_id"]
            self.assertEqual(len(runner.calls), 1)
            base = runner.calls[0][2]["storage_base"]
            self.assertIn(str(home / "tenants" / "tenant_a"), base)

            self.assertEqual(ca.get("/v1/console/video/jobs").json()["total"], 1)
            listed_b = cb.get("/v1/console/video/jobs").json()
            self.assertEqual(listed_b["total"], 0)
            self.assertEqual(cb.get(f"/v1/console/video/jobs/{job_id}").status_code, 404)
            self.assertEqual(cb.get(f"/v1/console/video/videos/{job_id}/download").status_code, 404)
            self.assertEqual(ca.get(f"/v1/console/video/jobs/{job_id}").json()["status"], "pending")
            self.assertEqual(ca.get("/v1/console/video/overview").json()["jobs_total"], 1)
            self.assertEqual(cb.get("/v1/console/video/overview").json()["jobs_total"], 0)

    # ── A2 ──────────────────────────────────────────────────────────────────
    def test_settings_are_per_tenant_and_cannot_redirect_output(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, _) = clients["tenant_a"], clients["tenant_b"]
            s = ca.get("/v1/console/video/settings").json()
            self.assertEqual(s["tts_engines"], ["openai", "auto", "gtts"])
            self.assertEqual(s["tts_engine"], "openai", "OpenAI TTS must be the default narration engine")
            self.assertTrue(s["openai_configured"])
            self.assertIn("web_slides_available", s)
            self.assertFalse(s["output_folder_editable"])
            self.assertIn(str(home / "tenants" / "tenant_a"), s["output_folder"])

            evil = str(home / "tenants" / "tenant_a" / "global" / "forge")
            r = ca.put("/v1/console/video/settings", json={"output_folder": evil}, headers={"X-CSRF-Token": csrf_a})
            self.assertEqual(r.status_code, 400, r.text)
            for bad in ({"tts_engine": "azure"}, {"max_duration_minutes": 0}, {"max_duration_minutes": 10**9}):
                r = ca.put("/v1/console/video/settings", json=bad, headers={"X-CSRF-Token": csrf_a})
                self.assertEqual(r.status_code, 422, (bad, r.text))
            # echoing the current (fixed) folder back is accepted — the UI resends the form
            r = ca.put("/v1/console/video/settings",
                       json={"output_folder": s["output_folder"], "max_duration_minutes": 5},
                       headers={"X-CSRF-Token": csrf_a})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(ca.get("/v1/console/video/settings").json()["max_duration_minutes"], 5)
            self.assertEqual(cb.get("/v1/console/video/settings").json()["max_duration_minutes"], 60)

    # ── A4 / A3 ─────────────────────────────────────────────────────────────
    def test_unbuilt_endpoints_answer_501_not_fabricated_success(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            job_id = self._create(client, csrf).json()["job_id"]
            # no video yet → 404; the point is it never says "queued"
            r = client.post(f"/v1/console/video/jobs/{job_id}/youtube", headers={"X-CSRF-Token": csrf})
            self.assertNotEqual(r.status_code, 200)
            self.assertNotIn("queued", r.text)
            for path in ("/v1/console/video/learning/models", "/v1/console/video/learning/confidence"):
                self.assertEqual(client.get(path).status_code, 501, path)
            for path in ("/v1/console/video/learning/select-model", "/v1/console/video/learning/report-quality"):
                self.assertEqual(client.post(path, json={}, headers={"X-CSRF-Token": csrf}).status_code, 501, path)
            stats = client.get("/v1/console/video/learning/stats")
            self.assertEqual(stats.status_code, 200, stats.text)
            self.assertEqual(stats.json()["feedback_stats"]["total_events"], 0)

    # ── A5 / A6 / A7 / A8 / A9 ──────────────────────────────────────────────
    def test_scene_feedback_is_validated_recorded_and_read_back_per_tenant(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            job_id = self._create(ca, csrf_a).json()["job_id"]
            url = f"/v1/console/video/jobs/{job_id}/scenes/s01/feedback"

            # a job that has not run yet cannot be rated: ADR-0534 wants a real execution first
            early = ca.post(url, json={"feedback_type": "approve"}, headers={"X-CSRF-Token": csrf_a})
            self.assertEqual(early.status_code, 503, early.text)
            self.assertIn("once it has been produced", early.json()["detail"])
            _finish_job(home, "tenant_a", job_id)

            for bad in ({"feedback_type": "bogus"}, {"feedback_type": "approve", "confidence": 7},
                        {"feedback_type": "approve", "quality_rating": 99}):
                r = ca.post(url, json=bad, headers={"X-CSRF-Token": csrf_a})
                self.assertEqual(r.status_code, 422, (bad, r.text))

            secret_reason = "contact me at alice@example.com please"
            r = ca.post(url, json={"feedback_type": "approve", "confidence": 0.9, "quality_rating": 5,
                                   "reason": secret_reason}, headers={"X-CSRF-Token": csrf_a})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["status"], "recorded")
            self.assertTrue(body["audit_ref"])
            self.assertNotIn("reason", body)

            m = ca.get(f"/v1/console/video/jobs/{job_id}/learning-metrics").json()
            self.assertEqual(m["total_feedback_events"], 1, m)
            self.assertEqual(m["approved"], 1)
            self.assertEqual(m["events"][0]["scene_id"], "s01")
            self.assertEqual(ca.get("/v1/console/video/learning/stats").json()["feedback_stats"]["positive"], 1)

            # tenant B sees nothing of A's feedback and cannot post to A's job
            self.assertEqual(cb.get(f"/v1/console/video/jobs/{job_id}/learning-metrics").json()["total_feedback_events"], 0)
            r = cb.post(url, json={"feedback_type": "reject"}, headers={"X-CSRF-Token": csrf_b})
            self.assertEqual(r.status_code, 404, r.text)

            # the free-text reason is nowhere at rest — not in events, not on the chain
            at_rest = _chain_text(home) + "".join(
                p.read_text(errors="replace") for p in (home / "tenants").rglob("*") if p.is_file()
                and p.suffix in (".jsonl", ".json"))
            self.assertNotIn("alice@example.com", at_rest)
            self.assertNotIn("contact me", at_rest)

    def test_job_level_feedback_records_or_404s(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            job_id = self._create(client, csrf).json()["job_id"]
            r = client.post("/v1/console/video/jobs/job_deadbeef/feedback", json={"scene_id": "s1", "rating": 3},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 404, r.text)
            r = client.post(f"/v1/console/video/jobs/{job_id}/feedback", json={"scene_id": "s1", "rating": 9},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 422, r.text)
            r = client.post(f"/v1/console/video/jobs/{job_id}/feedback", json={"scene_id": "s1", "rating": 4},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 503, r.text)  # the job has not run yet
            _finish_job(_home, "_default", job_id)
            r = client.post(f"/v1/console/video/jobs/{job_id}/feedback", json={"scene_id": "s1", "rating": 4},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["audit_ref"])
            self.assertEqual(client.get(f"/v1/console/video/jobs/{job_id}/learning-metrics").json()["total_feedback_events"], 1)

    # ── A12 ─────────────────────────────────────────────────────────────────
    def test_no_runner_refuses_and_failed_start_marks_error_without_leaking(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            mod = _route_module()
            mod.get_runner = None
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 503, r.text)
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 0)

            mod.get_runner = lambda: _RecordingRunner(fail=True)
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 500, r.text)
            self.assertNotIn("/home/x", r.text)
            jobs = client.get("/v1/console/video/jobs").json()["jobs"]
            self.assertEqual([j["status"] for j in jobs], ["error"])

    # ── B8: the real pre-spawn gates ────────────────────────────────────────
    def _deny_host(self, home, host):
        # allow everything except one host — the L44 classifier host stays reachable, so a
        # refusal must come from L35 itself
        _write_tenant_yaml(home, "_default", {"egress": {
            "enabled": True, "default_action": "allow", "allowed_hosts": [], "forbidden_hosts": [host],
        }})

    def test_egress_gate_follows_the_narration_engine_of_the_job(self):
        """The narration goes to the TTS provider on every job, so the L35 gate must name that
        provider's host: api.openai.com for the default engine, not translate.google.com."""
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner

            def put_engine(engine):
                r = client.put("/v1/console/video/settings", json={"tts_engine": engine}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 200, r.text)

            # default (openai): forbidding OpenAI refuses the job before it exists
            self._deny_host(home, "api.openai.com")
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(runner.calls, [])
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 0)
            self.assertIn("egress.blocked", _chain_text(home))
            self.assertIn("api.openai.com", _chain_text(home))

            # ... while forbidding only Google no longer matters for the default engine
            self._deny_host(home, "translate.google.com")
            self.assertEqual(self._create(client, csrf).status_code, 200)
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(runner.calls[0][2]["tts_engine"], "openai")

            # gtts: the Google host is the one that counts
            put_engine("gtts")
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(len(runner.calls), 1)

            # auto: the edge-tts tier's host must be admitted too
            put_engine("auto")
            self._deny_host(home, "speech.platform.bing.com")
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(len(runner.calls), 1)
            self._deny_host(home, "translate.google.com")
            self.assertEqual(self._create(client, csrf).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["tts_engine"], "auto")

    def test_storyboard_uses_the_hosts_claude_login_only_when_the_gates_admit_anthropic(self):
        """No API key on this kind of host: the storyboard goes to the Claude Code CLI the
        console already runs (Sonnet, measured), and stays on local Ollama when there is
        no CLI or when L35 forbids api.anthropic.com."""
        from unittest import mock

        fake = self._tmp / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        with _sandbox(self._tmp) as (client, csrf, home, _clients), \
                mock.patch.dict(os.environ, {"CORVIN_CLAUDE_BIN": str(fake)}):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner

            self.assertEqual(self._create(client, csrf).status_code, 200)
            cfg = runner.calls[-1][2]
            self.assertEqual(cfg["storyboard_backend"], "claude_cli")
            self.assertTrue(cfg["storyboard_model"].startswith("claude-sonnet-"), cfg["storyboard_model"])

            self._deny_host(home, "api.anthropic.com")
            self.assertEqual(self._create(client, csrf).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["storyboard_backend"], "ollama")
            self.assertIsNone(runner.calls[-1][2]["storyboard_model"])

            _write_tenant_yaml(home, "_default", {"egress": {"enabled": False}})
            os.environ["CORVIN_CLAUDE_BIN"] = str(self._tmp / "no-such-claude")
            self.assertEqual(self._create(client, csrf).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["storyboard_backend"], "ollama")

    def test_secret_in_task_is_refused_by_l34(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            _write_tenant_yaml(home, "_default", {"egress": {"enabled": False}})
            r = self._create(client, csrf, task="Make a video about our server. password = hunter2secret")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(runner.calls, [])
            self.assertIn("data_flow.blocked", _chain_text(home))

    def test_task_is_bounded(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            _route_module().get_runner = lambda: _RecordingRunner()
            r = self._create(client, csrf, task="x" * 4001)
            self.assertEqual(r.status_code, 422, r.text)


class VideoProducerPluginGateE2E(unittest.TestCase):
    """The API lives and dies with the plugin: installed + enabled for the tenant."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._restore: list = []

    def tearDown(self):
        for obj, attr, orig in reversed(self._restore):
            setattr(obj, attr, orig)

    def _benign_l44(self):
        import house_rules as _hr  # type: ignore  # on sys.path via corvin_console._spawn_gates
        self._restore.append((_hr, "_house_rules_classifier", _hr._house_rules_classifier))
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 0.99, "benign (test stub)")

    def _post(self, client, csrf):
        return client.post("/v1/console/video/jobs", json={"task": "x"}, headers={"X-CSRF-Token": csrf})

    def test_not_installed_and_installed_but_disabled_are_refused_before_any_work(self):
        for state in ("absent", "installed"):
            with _sandbox(self._tmp / state, plugin=state) as (client, csrf, _home, _c):
                for resp in (client.get("/v1/console/video/jobs"), self._post(client, csrf),
                             client.get("/v1/console/video/overview")):
                    self.assertEqual(resp.status_code, 404, f"{state}: {resp.text}")
                    self.assertIn("not installed and enabled", resp.text)

    def test_enabled_plugin_passes_the_gate(self):
        with _sandbox(self._tmp, plugin="enabled") as (client, _csrf, _home, _c):
            self.assertEqual(client.get("/v1/console/video/jobs").status_code, 200)

    def test_source_is_loaded_lazily_when_it_was_missing_at_import(self):
        # A fresh install imports the console BEFORE anything is installed: simulate that
        # state (nothing loaded) and check the first gated request loads the plugin.
        with _sandbox(self._tmp, plugin="enabled") as (client, _csrf, _home, _c):
            mod = _route_module()
            mod.VideoJob = mod._get_storage = mod.get_runner = mod.PLUGIN_SOURCE = None
            self.assertEqual(client.get("/v1/console/video/jobs").status_code, 200)
            self.assertIsNotNone(mod._get_storage)
            self.assertIsNotNone(mod.PLUGIN_SOURCE)

    def test_missing_runtime_dependencies_are_refused_by_name_before_a_job_exists(self):
        import shutil
        from unittest import mock

        with _sandbox(self._tmp, plugin="enabled") as (client, csrf, home, _c):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            real_find, real_which = mod.importlib.util.find_spec, shutil.which
            # default engine (openai): its package, Pillow and ffmpeg — gTTS is NOT needed any more
            self._benign_l44()
            for gone, needle in (("openai", "openai"), ("PIL", "Pillow"), ("ffmpeg", "ffmpeg")):
                with mock.patch.object(mod.importlib.util, "find_spec",
                                       lambda n, *a, _g=gone, **k: None if n == _g else real_find(n, *a, **k)), \
                     mock.patch.object(mod.shutil, "which",
                                       lambda n, *a, _g=gone, **k: None if n == _g else real_which(n, *a, **k)):
                    resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                                       headers={"X-CSRF-Token": csrf})
                self.assertEqual(resp.status_code, 503, resp.text)
                self.assertIn(needle, resp.text)
            with mock.patch.object(mod.importlib.util, "find_spec",
                                   lambda n, *a, **k: None if n == "gtts" else real_find(n, *a, **k)):
                self._benign_l44()
                resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                                   headers={"X-CSRF-Token": csrf})
            self.assertEqual(resp.status_code, 200, "the default engine must not need gTTS: " + resp.text)
            # gtts selected: now gTTS is required
            client.put("/v1/console/video/settings", json={"tts_engine": "gtts"}, headers={"X-CSRF-Token": csrf})
            with mock.patch.object(mod.importlib.util, "find_spec",
                                   lambda n, *a, **k: None if n == "gtts" else real_find(n, *a, **k)):
                resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                                   headers={"X-CSRF-Token": csrf})
            self.assertEqual(resp.status_code, 503, resp.text)
            self.assertIn("gTTS", resp.text)
            # nothing was stored for the refused attempts
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 1)

    def test_default_engine_without_an_openai_key_is_refused_with_the_way_out(self):
        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, _home, _c):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            self.assertFalse(client.get("/v1/console/video/settings").json()["openai_configured"])
            resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                               headers={"X-CSRF-Token": csrf})
            self.assertEqual(resp.status_code, 503, resp.text)
            self.assertIn("OpenAI TTS key", resp.text)
            self.assertIn("settings", resp.text)
            self.assertNotIn("sk-", resp.text)
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 0)
            # the other engines still work without it
            client.put("/v1/console/video/settings", json={"tts_engine": "auto"}, headers={"X-CSRF-Token": csrf})
            self._benign_l44()
            resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                               headers={"X-CSRF-Token": csrf})
            self.assertEqual(resp.status_code, 200, resp.text)

    def test_captions_endpoint_is_gone_and_new_jobs_have_no_subtitle_output(self):
        with _sandbox(self._tmp) as (client, _csrf, _home, _c):
            r = client.get("/v1/console/video/videos/job_deadbeef/captions")
            self.assertEqual(r.status_code, 404)
            self.assertNotIn("captions", {getattr(rt, "path", "").rsplit("/", 1)[-1] for rt in _route_module().router.routes})

    def test_the_github_cache_is_a_plugin_location(self):
        # On a fresh install the synced GitHub copy is the only place the source exists.
        with _sandbox(self._tmp, plugin="absent") as (_c, _t, home, _cl):
            locs = {kind: src for src, kind in _route_module()._plugin_locations()}
            self.assertEqual(
                locs["github-cache"],
                home / "marketplace-cache" / "Corvin-Marketplace" / "plugins" / "contributor"
                / "media" / "video_producer" / "src",
            )


if __name__ == "__main__":
    unittest.main()
