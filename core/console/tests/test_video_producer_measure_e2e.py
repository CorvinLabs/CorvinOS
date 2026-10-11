"""HTTP E2E: POST /video/jobs/{id}/measure - the post-hoc cue_alignment signal (ADR-2247, T-0094).

Driven through the REAL console router (TestClient, real session cookie + CSRF, two tenants, a temp
CORVIN_HOME) - the same harness as test_video_producer_routes_e2e. Real: the route, the plugin's
signals engine and report writer, the L44/L34/L35 pre-spawn gates (only the L44 classifier LEAF is
stubbed benign, the repo's convention), the tenant audit chain. Replaced: only the whisper-1 HTTP call -
it answers from a RECORDED response (``fixtures/video_producer/whisper_ldd_de_scenes_3_4.json``: real
whisper-1 output for two scenes of the published German LDD video, recorded 2026-10-11/12, provenance
inside), and every outbound ``requests`` call fails the test, so nothing reaches the network.

Covered: a measurement stored in the job's report and visible through /quality-metrics; audit-first
``measure_started`` before ``measure_completed`` (no narration in the chain, chain verifies); a refusal
by L35 (OpenAI host forbidden) and by L34 (a grounded job's narration is INTERNAL; the policy keeps
INTERNAL out of the US cloud) is a ``skip`` with its reason, ``measure_refused`` and no whisper call;
no key = skip; tenant isolation (B gets 404), no session (401), no CSRF token (403), unfinished job (409).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _route_module, _sandbox, _write_tenant_yaml  # noqa: E402

V = "/v1/console/video"
_PKG = "corvin_video_producer_plugin"
FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "video_producer"
                      / "whisper_ldd_de_scenes_3_4.json").read_text(encoding="utf-8"))
JOB = "job_7e5a1c00"


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _all_chains(home: Path) -> str:
    return "\n".join(f.read_text() for f in home.rglob("audit.jsonl"))


def _events(home: Path, tenant: str, prefix: str = "video_producer.measure_") -> list:
    p = _chain(home, tenant)
    out = []
    for line in (p.read_text().splitlines() if p.exists() else []):
        r = json.loads(line)
        if str(r.get("event_type") or "").startswith(prefix):
            out.append((r["event_type"], r.get("details") or {}))
    return out


def _produced_job(home: Path, tenant: str, job_id: str = JOB, *, grounded: bool = False, status: str = "complete") -> Path:
    """A finished job of ``tenant`` as the producer leaves it: storyboard (the recorded video's own scenes),
    output metadata with its cue log, report.json, and one narration file per scene."""
    mod = _route_module()
    models = importlib.import_module(f"{_PKG}.models")
    rp = importlib.import_module(f"{_PKG}.report")
    scenes = [models.Scene(id=f"s{i}", kind="narration", duration_ms=12000, narration_text=s["narration_text"],
                           template=s["template"], data=s["data"], beats=s["beats"])
              for i, s in enumerate(FIXTURE["storyboard"]["scenes"], start=1)]
    sb = models.Storyboard(id="sb_fixture", task="LDD", scenes=scenes)
    storage = mod._get_storage(str(home / "tenants" / tenant / "video_producer"))
    storage.save_job(mod.VideoJob(id=job_id, task="Explain LDD.", status=status, storyboard=sb, completed_at=datetime.now()))
    job_dir = Path(storage.videos_dir) / job_id
    (job_dir / "scenes").mkdir(parents=True, exist_ok=True)
    for i in range(1, len(scenes) + 1):
        (job_dir / "scenes" / f"scene_{i:03d}.mp3").write_bytes(b"narration audio (the recorded response stands in)")
    meta = {"cues": FIXTURE["cues"], "report": "report.json",
            "grounding": {"status": "grounded", "unverified": {"count": 0, "scenes": []}} if grounded else None}
    storage.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(job_dir / "output.mp4"),
                                                 metadata=meta, created_at=datetime.now()))
    rep = rp.ProductionReport(job_id, configured_tts="openai")
    for i in range(1, len(scenes) + 1):
        rep.scene(i, renderer="web")
    rep.finish("complete")
    rep.write(job_dir)
    return job_dir


class MeasureE2E(unittest.TestCase):
    setUp, tearDown, _benign_l44 = (_routes.VideoProducerRoutesE2E.setUp, _routes.VideoProducerRoutesE2E.tearDown,
                                   _routes.VideoProducerRoutesE2E._benign_l44)

    def _replay(self):
        """whisper-1 from the recording; the real HTTP path is cut so a slip would fail, not fetch."""
        sg = importlib.import_module(f"{_PKG}.signals")
        calls = []

        def whisper_words(mp3, api_key):
            self.assertEqual(api_key, os.environ["CORVIN_TTS_OPENAI_KEY"])
            calls.append(mp3.name)
            return FIXTURE["responses"][str(int(mp3.stem.split("_")[1]))]["response"]["words"]
        self._restore.append((sg, "whisper_words", sg.whisper_words))
        sg.whisper_words = whisper_words
        import requests

        def no_network(*a, **k):
            raise AssertionError("a test reached for the network")
        for name in ("post", "get"):
            self._restore.append((requests, name, getattr(requests, name)))
            setattr(requests, name, no_network)
        return calls

    def _post(self, client, csrf, job_id=JOB):
        self._benign_l44()
        return client.post(f"{V}/jobs/{job_id}/measure", headers={"X-CSRF-Token": csrf})

    def _verify_chain(self, home, tenant):
        import forge.security_events as se  # type: ignore[import-not-found]

        ok, problems = se.verify_chain(_chain(home, tenant))
        self.assertTrue(ok, problems)

    def test_measurement_is_stored_audited_first_and_tenant_scoped(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            calls = self._replay()
            job_dir = _produced_job(home, "tenant_a")

            # another tenant, no session, no CSRF token: nothing runs, nothing is sent
            self.assertEqual(self._post(cb, csrf_b).status_code, 404)
            anon = ca.__class__(ca.app, raise_server_exceptions=False)
            self.assertEqual(anon.post(f"{V}/jobs/{JOB}/measure").status_code, 401)
            self.assertEqual(ca.post(f"{V}/jobs/{JOB}/measure").status_code, 403)
            self.assertEqual(calls, [])

            r = self._post(ca, csrf_a)
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["measured"] and body["stored"])
            self.assertEqual(calls, ["scene_001.mp3", "scene_002.mp3"])
            sig = body["signals"][0]
            # nine cued items, all heard: 0.48 0.54 0.73 0.83 1.04 1.05 1.3 1.55 5.32 s
            self.assertEqual((sig["id"], sig["value"], sig["basis"], sig["verdict"]), ("cue_alignment", 1.04, {"n": 9, "of": 9}, "warn"))
            self.assertEqual((sig["detail"]["p90_s"], sig["detail"]["coverage"]), (5.32, 1.0))

            # the same record reaches the Quality tab through the report
            q = ca.get(f"{V}/jobs/{JOB}/quality-metrics").json()["report"]
            ca_sig = {s["id"]: s for s in q["signals"]}["cue_alignment"]
            self.assertEqual((ca_sig["value"], ca_sig["verdict"]), (1.04, "warn"))
            self.assertIn("posthoc_at", json.loads((job_dir / "report.json").read_text()))

            ev = _events(home, "tenant_a")
            self.assertEqual([e for e, _ in ev], ["video_producer.measure_started", "video_producer.measure_completed"])
            started, done = ev[0][1], ev[1][1]
            self.assertEqual((started["job_id"], started["engine"], started["classification"], started["scenes"]),
                             (JOB, "video_producer_openai", "classified", 2))
            self.assertEqual((done["verdict"], done["matched"], done["items"], done["median_ms"], done["p90_ms"], done["stored"]),
                             ("warn", 9, 9, 1040, 5320, True))
            chain = _chain(home, "tenant_a").read_text()
            for sc in FIXTURE["storyboard"]["scenes"]:
                self.assertNotIn(sc["narration_text"][:30], chain)
            self.assertEqual(_events(home, "tenant_b"), [])
            self._verify_chain(home, "tenant_a")

    def test_an_l35_refusal_is_a_skip_with_its_reason_and_no_audio_leaves(self):
        with _sandbox(self._tmp, tenants=("_default",)) as (client, csrf, home, _clients):
            calls = self._replay()
            job_dir = _produced_job(home, "_default")
            _write_tenant_yaml(home, "_default", {"egress": {
                "enabled": True, "default_action": "allow", "allowed_hosts": [], "forbidden_hosts": ["api.openai.com"]}})
            r = self._post(client, csrf)
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertFalse(body["measured"])
            self.assertTrue(body["refusal"])
            sig = body["signals"][0]
            self.assertEqual((sig["verdict"], sig["value"], sig["reason"]),
                             ("skip", None, "refused by the pre-spawn gates (L44/L34/L35)"))
            self.assertEqual(calls, [])
            stored = {s["id"]: s for s in json.loads((job_dir / "report.json").read_text())["signals"]}
            self.assertEqual(stored["cue_alignment"]["verdict"], "skip")
            ev = _events(home, "_default")
            self.assertEqual([e for e, _ in ev], ["video_producer.measure_refused"])
            self.assertEqual({k: v for k, v in ev[0][1].items() if k != "chain_dna"},
                             {"tenant_id": "_default", "job_id": JOB, "signal": "cue_alignment", "gate": "pre_spawn",
                              "stored": True})
            self.assertIn("egress.blocked", _all_chains(home))  # the L35 gate audits its own deny
            self._verify_chain(home, "_default")

    def test_a_grounded_job_is_gated_as_internal_like_its_pack(self):
        """The job's pack went to OpenAI's TTS only under the INTERNAL class; its audio may go to whisper
        only under the same class. A policy keeping INTERNAL out of the US cloud refuses the grounded
        job and admits the same video without grounding."""
        with _sandbox(self._tmp, tenants=("_default",)) as (client, csrf, home, _clients):
            calls = self._replay()
            _write_tenant_yaml(home, "_default", {"data_classification": {"matrix": {
                "PUBLIC": ["local", "eu_cloud", "us_cloud"], "INTERNAL": ["local", "eu_cloud"],
                "CONFIDENTIAL": ["local"], "SECRET": ["local"]}}})
            _produced_job(home, "_default", "job_7e5a1c01", grounded=True)
            _produced_job(home, "_default", "job_7e5a1c02", grounded=False)
            r1 = self._post(client, csrf, "job_7e5a1c01").json()
            self.assertFalse(r1["measured"])
            self.assertEqual(r1["signals"][0]["verdict"], "skip")
            self.assertEqual(calls, [])
            self.assertIn("data_flow.blocked", _all_chains(home))  # the L34 gate audits its own deny
            r2 = self._post(client, csrf, "job_7e5a1c02").json()
            self.assertTrue(r2["measured"], r2)
            self.assertEqual(len(calls), 2)
            self.assertEqual([e for e, _ in _events(home, "_default")],
                             ["video_producer.measure_refused", "video_producer.measure_started",
                              "video_producer.measure_completed"])
            self._verify_chain(home, "_default")

    def test_no_key_is_a_skip_and_an_unfinished_job_is_refused(self):
        with _sandbox(self._tmp, tenants=("_default",)) as (client, csrf, home, _clients):
            calls = self._replay()
            _produced_job(home, "_default", "job_7e5a1c03", status="skills_running")
            self.assertEqual(self._post(client, csrf, "job_7e5a1c03").status_code, 409)
            _produced_job(home, "_default")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CORVIN_TTS_OPENAI_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)
                body = self._post(client, csrf).json()
            self.assertFalse(body["measured"])
            self.assertEqual(body["signals"][0]["reason"], "no OpenAI key on this host")
            self.assertEqual(calls, [])
            self.assertEqual(_events(home, "_default")[-1][1]["gate"], "no_key")

    def test_a_revision_hands_the_base_videos_narrations_to_the_job(self):
        """revision_locality (information) needs the base storyboard in the job: the route passes the base
        video's narrations as ``revision_base_narrations``; a first version gets none."""
        with _sandbox(self._tmp, tenants=("_default",)) as (client, csrf, home, _clients):
            runner = _routes._RecordingRunner()
            _route_module().get_runner = lambda: runner
            _produced_job(home, "_default")
            self._benign_l44()
            r = client.post(f"{V}/jobs", json={"task": "Make scene two shorter.", "base_job_id": JOB},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertEqual(cfg["revision_base_narrations"],
                             [s["narration_text"] for s in FIXTURE["storyboard"]["scenes"]])
            r = client.post(f"{V}/jobs", json={"task": "Explain the audit chain in two scenes."},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("revision_base_narrations", runner.calls[-1][2])


if __name__ == "__main__":
    unittest.main()
