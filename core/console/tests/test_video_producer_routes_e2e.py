"""HTTP E2E proof for the Video Producer console routes (adversarial review 2026-10-03).

Driven through the REAL console router (TestClient, real session cookie + CSRF,
two tenants, a temp CORVIN_HOME). Real: route handlers, the marketplace plugin
storage, the L44/L34/L35 pre-spawn gates, the learning EventStore and its
audit-chain write. Replaced: only the production runner (a real job calls an
LLM, Google TTS and ffmpeg — external egress a test must not cause); the
recorder below captures exactly what the route hands to it.
"""
from __future__ import annotations

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
def _sandbox(tmp_path, *, tenants=("_default",), plugin: str = "enabled"):
    """``test_admin_route._sandbox`` plus the plugin state the API is gated on
    (``plugin`` = "enabled" | "installed" | "absent")."""
    with _base_sandbox(tmp_path, tenants=tenants) as boxed:
        if plugin != "absent":
            for tenant in tenants:
                _install_plugin(tenant, enable=plugin == "enabled")
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
            self.assertEqual(s["tts_engines"], ["gtts"])
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
    def test_egress_policy_denying_google_refuses_the_job_before_it_exists(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            # allow everything except Google TTS — the L44 classifier host stays
            # reachable, so the refusal must come from L35 itself
            _write_tenant_yaml(home, "_default", {"egress": {
                "enabled": True, "default_action": "allow",
                "allowed_hosts": [], "forbidden_hosts": ["translate.google.com"],
            }})
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(runner.calls, [])
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 0)
            self.assertIn("egress.blocked", _chain_text(home))

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
            for gone, needle in (("gtts", "gTTS"), ("PIL", "Pillow"), ("ffmpeg", "ffmpeg")):
                with mock.patch.object(mod.importlib.util, "find_spec",
                                       lambda n, *a, _g=gone, **k: None if n == _g else real_find(n, *a, **k)), \
                     mock.patch.object(mod.shutil, "which",
                                       lambda n, *a, _g=gone, **k: None if n == _g else real_which(n, *a, **k)):
                    resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                                       headers={"X-CSRF-Token": csrf})
                self.assertEqual(resp.status_code, 503, resp.text)
                self.assertIn(needle, resp.text)
            # nothing was stored for the refused attempts
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], 0)

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
