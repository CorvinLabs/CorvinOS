"""Isolated console/gateway backend for the Video Producer data-lifecycle Playwright spec (T-0092).

Started by ``playwright.video-lifecycle.config.ts`` as its ``webServer``. Same isolation as the
Quality-tab harness (``../../video-quality/harness/isolated_vp_backend.py``, whose sandbox and plugin
install it reuses): the REAL ``corvin_gateway.app`` with the built console ``dist/`` against a
throwaway ``CORVIN_HOME``; the live install on :8765 and its audit chain are never touched.

The job runner is the plugin's REAL ``ProcessJobRunner`` (one OS process per job, its cancel, its
reaper); only the job process is a stand-in (``core/console/tests/_vp_lifecycle_stub_job.py`` with
``[hang]``: it marks itself running and sleeps) - a real job calls an LLM, a TTS provider and ffmpeg.

Seed (records through the plugin's own storage, owners through the host's ``video_job_store``):

* ``job_de1e0001`` - a finished job of ``_default`` with a real MP4, scenes, audio and a report (delete target)
* ``job_cee00003`` - a finished job of ``_default`` (stays)
* ``job_c0de0002`` - a job of ``_default`` that is being produced (cancel target)
* ``job_0b0b0004`` - a job only in tenant ``tenant_b``'s store (cross-tenant negative)
* tenant ``_default`` quota: ``spec.video_producer.storage_quota_gb: 1``
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("VQ_E2E_HOME", os.environ.get("VL_E2E_HOME", "/tmp/corvin-vl-e2e"))
os.environ.setdefault("VQ_E2E_PORT", os.environ.get("VL_E2E_PORT", "8797"))
_spec = importlib.util.spec_from_file_location("vq_harness", HERE.parents[1] / "video-quality" / "harness" / "isolated_vp_backend.py")
vq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vq)

HOME, PORT, REPO = vq.HOME, vq.PORT, vq.REPO
STUB = REPO / "core/console/tests/_vp_lifecycle_stub_job.py"
JOB_DELETE, JOB_RUNNING, JOB_KEEP, JOB_OTHER = "job_de1e0001", "job_c0de0002", "job_cee00003", "job_0b0b0004"


def _seed() -> dict:
    from corvin_console.routes import video_producer_api as vpa

    assert vpa._ensure_plugin("_default"), "plugin not enabled for _default"
    models = sys.modules[vpa.VideoJob.__module__]
    runner_mod = sys.modules[f"{vpa._PLUGIN_PKG}.async_runner"]
    vjs = vpa._vjs()

    def finished(tenant: str, job_id: str, task: str) -> None:
        base = HOME / "tenants" / tenant / "video_producer"
        store = vpa._get_storage(str(base))
        sb = models.Storyboard(id="sb1", task=task, scenes=[
            models.Scene(id="s01", kind="title", duration_ms=4000, narration_text="Welcome."),
        ])
        now = datetime.now()
        store.save_job(vpa.VideoJob(id=job_id, task=task, status="complete", storyboard=sb, started_at=now, completed_at=now))
        job_dir = Path(store.videos_dir) / job_id
        out = job_dir / "output.mp4"
        vq._encode_mp4(out)
        for rel in ("scenes/scene_01.png", "audio/scene_01.mp3", "poster.jpg"):
            (job_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (job_dir / rel).write_bytes(b"\0" * 4096)
        (job_dir / "report.json").write_text(json.dumps({"job_id": job_id, "schema": 1}))
        store.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(out), metadata={"report": "report.json"}, created_at=now))
        vjs.record_owner(base, job_id, f"console-owner:{tenant}")

    finished("_default", JOB_KEEP, "Explain the release checklist.")
    finished("_default", JOB_DELETE, "Explain the audit chain.")
    finished("tenant_b", JOB_OTHER, "Explain tenant isolation.")

    # a job that is being produced: the real process runner, a stand-in job process that hangs
    base = HOME / "tenants" / "_default" / "video_producer"
    store = vpa._get_storage(str(base))
    store.save_job(vpa.VideoJob(id=JOB_RUNNING, task="Explain how a job is cancelled.", status="pending"))
    vjs.record_owner(base, JOB_RUNNING, "console-owner:_default")
    runner = runner_mod.ProcessJobRunner(entry=[sys.executable, str(STUB)])
    vpa.get_runner = lambda: runner
    asyncio.run(runner.start_job(JOB_RUNNING, "[hang] Explain how a job is cancelled.",
                                 {"storage_base": str(base), "max_duration_minutes": 5}))

    yml = HOME / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    yml.write_text("spec:\n  telemetry:\n    ping_enabled: false\n    healing_traces: false\n"
                   "  video_producer:\n    storage_quota_gb: 1\n")
    os.chmod(yml, 0o600)
    return {"job_delete": JOB_DELETE, "job_running": JOB_RUNNING, "job_keep": JOB_KEEP,
            "job_other_tenant": JOB_OTHER, "store": str(base), "tenant_yaml": str(yml)}


def main() -> None:
    vq._reset_home()
    vq._sandbox_env()
    for t in ("_default", "tenant_b"):
        vq._install_plugin(t)
    (HOME / "seed.json").write_text(json.dumps(_seed(), indent=1))

    import uvicorn
    from corvin_gateway.app import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
