"""HTTP E2E for the Video Producer data lifecycle (ADR-2247 R3, PLAN-0946 R3, T-0092).

Driven through the REAL console router (TestClient, real session cookie + CSRF, two tenants, a temp
CORVIN_HOME): owners.json written at job creation, ``DELETE /v1/console/video/jobs/{id}`` (409 while
the job is produced, 404 across tenants, every file of the job gone from disk, the sidecar entries
gone, ``video_producer.job_deleted`` in the tenant chain and the chain verifies), the per-tenant
storage quota (refused job, usage in /overview), and the GDPR Art. 17 erasure through the real CCC
``/erase`` dispatch (the real ErasureOrchestrator and handler chain) removing exactly the subject's
jobs. Replaced: only the production runner (a recorder - a real job calls an LLM, TTS and ffmpeg).
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _RecordingRunner, _finish_job, _route_module, _sandbox, _write_tenant_yaml  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
_REPO = Path(__file__).resolve().parents[3]
TASK = "Explain the quarterly review in two short scenes."


def _store_dir(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "video_producer"


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _records(home: Path, tenant: str, event: str) -> list:
    p = _chain(home, tenant)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if (r.get("event") or r.get("event_type")) == event:
                out.append(r)
    return out


def _vjs():
    shared = str(_REPO / "corvin_operator" / "bridges" / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import video_job_store

    return video_job_store


def _produce_artifacts(home: Path, tenant: str, job_id: str, *, metadata: dict = None) -> dict:
    """What a finished job leaves in its store: scenes, audio, a poster, a cache, the MP4, the report,
    the video metadata, a thumbnail - written where the plugin writes them, through its storage."""
    mod = _route_module()
    models = sys.modules[mod.VideoJob.__module__]
    storage = mod._get_storage(str(_store_dir(home, tenant)))
    vdir = Path(storage.videos_dir) / job_id
    files = {
        "scene": vdir / "scenes" / "scene_01.png",
        "audio": vdir / "audio" / "scene_01.mp3",
        "poster": vdir / "poster.jpg",
        "cache": vdir / ".cache" / "web" / "frame_0001.png",
        "video": vdir / "output.mp4",
        "report": vdir / "report.json",
    }
    for name, p in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 2048 if name != "report" else json.dumps({"job_id": job_id, "schema": 1}).encode())
    storage.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(files["video"]),
                                                 metadata=metadata or {"report": "report.json"},
                                                 created_at=datetime.now()))
    thumb = Path(storage.thumbnails_dir) / f"{job_id}.png"
    thumb.write_bytes(b"t" * 100)
    files["thumbnail"] = thumb
    files["metadata"] = vdir / "metadata.json"
    files["record"] = Path(storage.jobs_dir) / f"{job_id}.json"
    return files


class DataLifecycleE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _job(self, client, csrf, task=TASK):
        self._benign_l44()
        r = client.post(f"{V}/jobs", json={"task": task}, headers=H(csrf))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["job_id"]

    # ── owner + DELETE across two tenants ────────────────────────────────────
    def test_delete_is_tenant_scoped_refuses_a_running_job_and_removes_every_file(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            vjs = _vjs()
            done, running = self._job(ca, csrf_a), self._job(ca, csrf_a)
            b_job = self._job(cb, csrf_b)
            a_dir = _store_dir(home, "tenant_a")

            # owners.json: job id -> the principal that created it (local-login = the tenant's owner)
            owners = json.loads((a_dir / "owners.json").read_text())
            self.assertEqual(owners, {done: "console-owner:tenant_a", running: "console-owner:tenant_a"})
            self.assertEqual(oct((a_dir / "owners.json").stat().st_mode & 0o777), "0o600")
            self.assertEqual(vjs.owner_of(_store_dir(home, "tenant_b"), b_job), "console-owner:tenant_b")

            _finish_job(home, "tenant_a", done)
            files = _produce_artifacts(home, "tenant_a", done)
            vjs.record_revision(a_dir, running, done)  # `running` revises `done`
            for p in files.values():
                self.assertTrue(p.exists(), p)

            # tenant B cannot delete (or even see) A's job: 404, nothing removed
            r = cb.delete(f"{V}/jobs/{done}", headers=H(csrf_b))
            self.assertEqual(r.status_code, 404, r.text)
            self.assertTrue(all(p.exists() for p in files.values()))
            # no CSRF token: refused before the route runs
            self.assertEqual(ca.delete(f"{V}/jobs/{done}").status_code, 403)
            self.assertTrue(files["record"].exists())
            # a job that is still being produced: 409, kept
            r = ca.delete(f"{V}/jobs/{running}", headers=H(csrf_a))
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("cancel it first", r.json()["detail"])
            self.assertTrue((a_dir / "jobs" / f"{running}.json").exists())
            # a malformed id is a 404 like any absent job
            self.assertEqual(ca.delete(f"{V}/jobs/..%2Fowners", headers=H(csrf_a)).status_code, 404)

            r = ca.delete(f"{V}/jobs/{done}", headers=H(csrf_a))
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["deleted"])
            self.assertGreaterEqual(body["files_removed"], len(files))
            self.assertGreater(body["bytes_freed"], 5 * 2048)
            # every file of the job is gone from disk: record, its locks, video dir, report, thumbnail
            for name, p in files.items():
                self.assertFalse(p.exists(), f"{name} survived: {p}")
            self.assertFalse((a_dir / "videos" / done).exists())
            self.assertFalse(any((a_dir / "jobs").glob(f"*{done}*")))
            # its sidecar entries are gone, the other job's owner entry stays, the revision link is dropped
            self.assertEqual(json.loads((a_dir / "owners.json").read_text()), {running: "console-owner:tenant_a"})
            self.assertEqual(json.loads((a_dir / "revisions.json").read_text()), {})
            # the API agrees
            self.assertEqual(ca.get(f"{V}/jobs/{done}").status_code, 404)
            self.assertEqual({j["id"] for j in ca.get(f"{V}/jobs").json()["jobs"]}, {running})
            self.assertEqual(ca.delete(f"{V}/jobs/{done}", headers=H(csrf_a)).status_code, 404)
            # tenant B's job is untouched
            self.assertTrue((_store_dir(home, "tenant_b") / "jobs" / f"{b_job}.json").exists())
            self.assertEqual({j["id"] for j in cb.get(f"{V}/jobs").json()["jobs"]}, {b_job})

            # audited in A's chain: ids and counts only, never the task text; the chain verifies
            recs = _records(home, "tenant_a", "video_producer.job_deleted")
            self.assertEqual(len(recs), 1)
            d = recs[0]["details"]
            self.assertEqual((d["job_id"], d["status"], d["tenant_id"]), (done, "complete", "tenant_a"))
            self.assertEqual(set(d) - {"_dropped_fields", "chain_dna"}, {"tenant_id", "job_id", "status", "files_removed", "bytes_freed"})
            self.assertNotIn("quarterly", json.dumps(recs[0]))
            import forge.security_events as se  # type: ignore[import-not-found]

            ok, problems = se.verify_chain(_chain(home, "tenant_a"))
            self.assertTrue(ok, problems)

    def test_a_deletion_that_cannot_be_recorded_keeps_the_job(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            ca, csrf = clients["tenant_a"]
            job = self._job(ca, csrf)
            _finish_job(home, "tenant_a", job)
            files = _produce_artifacts(home, "tenant_a", job)
            orig = mod._audit
            mod._audit = lambda *a, **k: False
            try:
                r = ca.delete(f"{V}/jobs/{job}", headers=H(csrf))
            finally:
                mod._audit = orig
            self.assertEqual(r.status_code, 503, r.text)
            self.assertTrue(all(p.exists() for p in files.values()))

    # ── storage quota ────────────────────────────────────────────────────────
    def test_quota_refuses_a_new_job_beyond_it_and_overview_shows_usage(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            ca, csrf = clients["tenant_a"]
            ov = ca.get(f"{V}/overview").json()["storage"]
            self.assertEqual(ov["quota_gb"], 5.0)  # the default
            self.assertEqual(ov["quota_bytes"], 5 * 1024 ** 3)
            self.assertFalse(ov["full"])

            job = self._job(ca, csrf)
            _finish_job(home, "tenant_a", job)
            _produce_artifacts(home, "tenant_a", job)
            used = ca.get(f"{V}/overview").json()["storage"]["used_bytes"]
            self.assertGreater(used, 5 * 2048)
            on_disk = sum(p.lstat().st_size for p in _store_dir(home, "tenant_a").rglob("*") if p.is_file())
            self.assertEqual(used, on_disk)

            # a quota below what the store holds: the next job is refused with a clear message
            _write_tenant_yaml(home, "tenant_a", {"video_producer": {"storage_quota_gb": 0.00001}})
            ov = ca.get(f"{V}/overview").json()["storage"]
            self.assertTrue(ov["full"])
            self.assertEqual(ov["quota_bytes"], int(0.00001 * 1024 ** 3))
            self._benign_l44()
            n_before = len(list((_store_dir(home, "tenant_a") / "jobs").glob("job_*.json")))
            r = ca.post(f"{V}/jobs", json={"task": TASK}, headers=H(csrf))
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("Video storage is full", r.json()["detail"])
            self.assertIn("Delete videos", r.json()["detail"])
            self.assertEqual(len(list((_store_dir(home, "tenant_a") / "jobs").glob("job_*.json"))), n_before)

            # deleting frees the space and production is possible again
            self.assertEqual(ca.delete(f"{V}/jobs/{job}", headers=H(csrf)).status_code, 200)
            _write_tenant_yaml(home, "tenant_a", {"video_producer": {"storage_quota_gb": 0.001}})
            self.assertFalse(ca.get(f"{V}/overview").json()["storage"]["full"])
            self._job(ca, csrf)
            # a nonsense quota never lifts the limit: it reads as the default
            _write_tenant_yaml(home, "tenant_a", {"video_producer": {"storage_quota_gb": "unlimited"}})
            self.assertEqual(ca.get(f"{V}/overview").json()["storage"]["quota_gb"], 5.0)

    # ── GDPR Art. 17 ─────────────────────────────────────────────────────────
    def test_erasure_through_the_real_route_removes_exactly_the_subjects_jobs(self):
        subject = "discord:4242"
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            vjs = _vjs()
            a_dir, b_dir = _store_dir(home, "tenant_a"), _store_dir(home, "tenant_b")
            owned, named, unrelated = self._job(ca, csrf_a), self._job(ca, csrf_a), self._job(ca, csrf_a)
            legacy = self._job(ca, csrf_a, task=f"Explain what {subject} asked about the audit chain.")
            other_tenant = self._job(cb, csrf_b)
            for j in (owned, named, unrelated, legacy):
                _finish_job(home, "tenant_a", j)
            arts = {
                owned: _produce_artifacts(home, "tenant_a", owned),
                # a job whose stored record names the subject under an identity key
                named: _produce_artifacts(home, "tenant_a", named, metadata={"requested_by": subject}),
                unrelated: _produce_artifacts(home, "tenant_a", unrelated),
                legacy: _produce_artifacts(home, "tenant_a", legacy),
            }
            # `owned` was created by the subject (a bridge user; written the way the route writes it),
            # `legacy` predates owners (no entry) and only mentions the subject in its free-text task
            vjs.record_owner(a_dir, owned, subject)
            vjs.update_maps(a_dir, lambda m: m["owners"].pop(legacy))
            vjs.record_revision(a_dir, unrelated, owned)
            vjs.record_owner(b_dir, other_tenant, subject)  # another tenant: a different erasure scope

            from corvin_console import chat_router
            from entity_extract import EntityPlan  # type: ignore[import-not-found]

            plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": subject}, confidence=1.0)
            # /erase asks for a one-time code first; take the one the router would have shown the user.
            plan.slots["confirm"] = chat_router._issue_erase_code("tenant_a", plan.slots["subject_id"])
            result = asyncio.run(chat_router.dispatch(plan, "tenant_a"))
            self.assertEqual(result.status, "created", result)
            layers = {lr["layer_id"]: lr for lr in result.payload["layers"]}
            self.assertEqual(layers["L-video-producer"]["status"], "applied", layers["L-video-producer"])
            self.assertNotIn(subject, json.dumps(result.payload))

            for gone in (owned, named):
                self.assertFalse((a_dir / "jobs" / f"{gone}.json").exists(), gone)
                self.assertFalse((a_dir / "videos" / gone).exists(), gone)
                self.assertFalse(any(p.exists() for p in arts[gone].values()), gone)
                self.assertEqual(ca.get(f"{V}/jobs/{gone}").status_code, 404)
            for kept in (unrelated, legacy):
                self.assertTrue(all(p.exists() for p in arts[kept].values()), kept)
            self.assertEqual({j["id"] for j in ca.get(f"{V}/jobs").json()["jobs"]}, {unrelated, legacy})
            owners = json.loads((a_dir / "owners.json").read_text())
            self.assertEqual(owners, {unrelated: "console-owner:tenant_a"})
            self.assertEqual(json.loads((a_dir / "revisions.json").read_text()), {})
            # the other tenant's store is a different scope and stays
            self.assertTrue((b_dir / "jobs" / f"{other_tenant}.json").exists())
            self.assertEqual(vjs.owner_of(b_dir, other_tenant), subject)

    def test_erasure_fails_loudly_while_a_matching_job_is_being_produced(self):
        subject = "discord:4242"
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            mod.get_runner = lambda: _RecordingRunner()
            ca, csrf = clients["tenant_a"]
            vjs = _vjs()
            a_dir = _store_dir(home, "tenant_a")
            live, done = self._job(ca, csrf), self._job(ca, csrf)
            _finish_job(home, "tenant_a", done)
            for j in (live, done):
                vjs.record_owner(a_dir, j, subject)
            storage_mod = sys.modules[f"{mod._PLUGIN_PKG}.storage"]
            lock = storage_mod.hold_job_lock(a_dir / "jobs", live)  # what the runner holds while a job runs
            try:
                import erasure_handlers as eh  # type: ignore[import-not-found]

                res = eh.VideoProducerHandler(tenant_id="tenant_a").purge(subject, "er-live")
            finally:
                storage_mod.release_job_lock(lock)
            self.assertEqual(getattr(res.status, "value", res.status), "failed")
            self.assertIn("still being produced", res.reason)
            self.assertTrue((a_dir / "jobs" / f"{live}.json").exists())
            self.assertFalse((a_dir / "jobs" / f"{done}.json").exists())
            # once it has ended, the same request removes it
            res = eh.VideoProducerHandler(tenant_id="tenant_a").purge(subject, "er-live-2")
            self.assertEqual(getattr(res.status, "value", res.status), "applied")
            self.assertFalse((a_dir / "jobs" / f"{live}.json").exists())


class SidecarLockTest(unittest.TestCase):
    """owners.json + revisions.json: concurrent writers from several threads and processes lose nothing."""

    def test_concurrent_writers_lose_no_entry(self):
        import subprocess
        import threading

        vjs = _vjs()
        base = Path(tempfile.mkdtemp()) / "video_producer"
        base.mkdir()

        def burst(prefix: int) -> None:
            for i in range(25):
                vjs.record_owner(base, f"job_{prefix:02x}{i:06x}", f"user:{prefix}")

        child = ("import sys; sys.path.insert(0, sys.argv[1]); import video_job_store as v; from pathlib import Path\n"
                 "p = int(sys.argv[3])\n"
                 "for i in range(25): v.record_owner(Path(sys.argv[2]), f'job_{p:02x}{i:06x}', f'user:{p}')")
        shared = str(_REPO / "corvin_operator" / "bridges" / "shared")
        procs = [subprocess.Popen([sys.executable, "-c", child, shared, str(base), str(p)]) for p in range(4, 8)]
        threads = [threading.Thread(target=burst, args=(p,)) for p in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([p.wait(timeout=60) for p in procs], [0, 0, 0, 0])
        self.assertEqual(len(vjs.read_map(base, vjs.OWNERS_FILE)), 8 * 25)


if __name__ == "__main__":
    unittest.main()
