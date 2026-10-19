"""Isolated console/gateway backend for the Video Producer Quality-tab Playwright spec (T-0091).

Started by ``playwright.video-quality.config.ts`` as its ``webServer``. It runs the REAL
``corvin_gateway.app`` (the app ``corvin-webui.service`` serves, including the built console
``dist/``) against a throwaway ``CORVIN_HOME`` - the live install on :8765 and its audit chain are
never touched (CLAUDE.md "Tests never touch the live install"). Nothing is patched.

Seed, written through the plugin's own storage (the single write path):

* ``job_5e1a0001`` - a finished job of tenant ``_default`` with a real H.264/AAC MP4 (ffmpeg) and the
  production report a REAL job wrote (``core/console/tests/fixtures/video_producer/
  report_real_job_2026-10-11.json``; only its ``job_id`` is remapped);
* ``job_01d00002`` - a finished job of ``_default`` WITHOUT a report (a job from before 1.5.0);
* ``job_0b0b0003`` - a job that exists only in tenant ``tenant_b``'s store (cross-tenant negative).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
HOME = Path(os.environ.get("VQ_E2E_HOME", "/tmp/corvin-vq-e2e")).resolve()
PORT = int(os.environ.get("VQ_E2E_PORT", "8796"))
MARKER = ".vq-e2e-home"  # only a directory carrying this marker is ever wiped
FIXTURE = REPO / "core/console/tests/fixtures/video_producer/report_real_job_2026-10-11.json"
JOB_REPORT, JOB_OLD, JOB_OTHER = "job_5e1a0001", "job_01d00002", "job_0b0b0003"


def _main_checkout() -> Path:
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common.endswith(".git"):
            return Path(common).parent
    except (OSError, subprocess.SubprocessError):
        pass
    return REPO


def _reset_home() -> None:
    if HOME.exists():
        if not (HOME / MARKER).exists():
            sys.exit(f"refusing to wipe {HOME}: it is not a vq-e2e home (no {MARKER})")
        shutil.rmtree(HOME)
    for t in ("_default", "tenant_b"):
        (HOME / "tenants" / t / "global" / "forge").mkdir(parents=True, exist_ok=True)
    (HOME / "xdg").mkdir(parents=True, exist_ok=True)
    (HOME / MARKER).write_text("throwaway CORVIN_HOME for the video-quality Playwright spec\n")
    yml = HOME / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    yml.write_text("spec:\n  telemetry:\n    ping_enabled: false\n    healing_traces: false\n")
    os.chmod(yml, 0o600)


def _sandbox_env() -> None:
    for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TASK_ID", "CORVIN_CHANNEL_ID"):
        os.environ.pop(k, None)
    os.environ.update({
        "CORVIN_HOME": str(HOME),
        "CORVIN_TENANT_ID": "_default",
        "XDG_CONFIG_HOME": str(HOME / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(HOME / "audit-anchor.key"),
        "CORVIN_TELEMETRY_OPTIN": "false",
        "CORVIN_KB_REPO": str(HOME / "no-knowledge-base"),
    })
    os.chdir(HOME)
    # Plugin source: the sibling Corvin-Marketplace checkout (read-only). CORVIN_MARKETPLACE_ROOT,
    # when set, names its plugins/buildin directory (corvin_plugins.bootstrap._marketplace_root).
    if not os.environ.get("CORVIN_MARKETPLACE_ROOT"):
        cand = _main_checkout().parent / "Corvin-Marketplace" / "plugins" / "buildin"
        if cand.is_dir():
            os.environ["CORVIN_MARKETPLACE_ROOT"] = str(cand)
    for p in ("core/plugins", "corvin_operator/bridges/shared", "corvin_operator/skill-forge",
              "corvin_operator/forge", "core/compliance", "core/license", "core/gateway", "core/console", ""):
        sys.path.insert(0, str(REPO / p) if p else str(REPO))


def _install_plugin(tenant: str) -> None:
    from corvin_console.routes import marketplace_resolve as _resolve
    from corvin_plugins.state import PluginLifecycle

    plugin_dir, manifest = _resolve.load_manifest("plugin:buildin-media-corvintv")
    record = _resolve.record_from_manifest(manifest, plugin_dir=plugin_dir)
    lifecycle = PluginLifecycle(tenant_id=tenant, lifecycle_enabled=True)
    lifecycle.install(record, installed_by="vq-e2e")
    lifecycle.enable(record.plugin_id, consent_granted_by="vq-e2e")


def _encode_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True, timeout=60)


def _seed() -> dict:
    from corvin_console.routes import video_producer_api as vpa

    assert vpa._ensure_plugin("_default"), "plugin not enabled for _default"
    models = sys.modules[vpa.VideoJob.__module__]

    def job(tenant: str, job_id: str, *, video: bool, report: bool) -> None:
        store = vpa._get_storage(str(HOME / "tenants" / tenant / "video_producer"))
        sb = models.Storyboard(id="sb1", task="Explain the audit chain.", scenes=[
            models.Scene(id="s01", kind="title", duration_ms=8000, narration_text="Welcome to the audit chain."),
            models.Scene(id="s02", kind="narration", duration_ms=8000, narration_text="Every record is hash-linked."),
        ])
        now = datetime.now()
        store.save_job(vpa.VideoJob(id=job_id, task="Explain the audit chain.", status="complete", storyboard=sb,
                                    started_at=now, completed_at=now))
        job_dir = Path(store.videos_dir) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        out = job_dir / "output.mp4"
        if video:
            _encode_mp4(out)
        meta = {"tts_provider_used": "mock"}
        if report:
            data = json.loads(FIXTURE.read_text(encoding="utf-8"))
            data["job_id"] = job_id
            (job_dir / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
            meta["report"] = "report.json"
        store.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(out), metadata=meta, created_at=now))

    job("_default", JOB_REPORT, video=True, report=True)
    job("_default", JOB_OLD, video=False, report=False)
    job("tenant_b", JOB_OTHER, video=False, report=True)
    return {"job_report": JOB_REPORT, "job_old": JOB_OLD, "job_other_tenant": JOB_OTHER,
            "fixture": json.loads(FIXTURE.read_text(encoding="utf-8"))}


def main() -> None:
    _reset_home()
    _sandbox_env()
    for t in ("_default", "tenant_b"):
        _install_plugin(t)
    (HOME / "seed.json").write_text(json.dumps(_seed(), indent=1))

    import uvicorn
    from corvin_gateway.app import app

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
