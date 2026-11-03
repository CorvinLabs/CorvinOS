"""LIVE end-to-end proof for CorvinTV 1.8.0: a fresh CorvinOS home, a real console, a real video.

What it proves, with nothing mocked:

* **Fresh install, no manual step.** A brand-new ``CORVIN_HOME`` (a temp dir - never the operator's
  ``~/.corvin``) boots a real console (``corvin_console.standalone:create_app``, the entry point every
  install runs) on a free port. Its boot provisions the standard plugins (``default_plugins.yaml``) from
  the marketplace source, so CorvinTV must arrive installed, ENABLED and at version >= 1.8.0.
* **The console's own job path.** The job is created through ``POST /v1/console/video/jobs`` (L44/L34/L35
  gates, ``_storyboard_backend``, ``_job_env``, the process runner spawning ``job_main.py``) - exactly what
  the panel does.
* **Only native engines.** The storyboard is written by the signed-in ``claude`` CLI (report
  ``storyboard_llm`` = ``claude_cli:<model>``, configured backend ``claude_cli``); every scene is spoken by
  the CorvinOS TTS chain (``tts_chain`` = ``corvinos``, each scene's tier one of openai/edge/piper).
  Nothing in the job's report, metadata or the console log mentions another LLM engine.
* **A real artifact.** ``output.mp4`` exists and ffprobe (or, without ffprobe, the plugin's ffmpeg-only
  probe) measures a duration > 0 with a video AND an audio stream.

Isolation: HOME/USERPROFILE point into the temp dir (the launcher's auto-update is switched off there, so
the console never runs the updater on this checkout); the claude CLI keeps its login through
``CLAUDE_CONFIG_DIR``. The running console on :8765 is never contacted.

Run (costs one short Sonnet call per scene budget and a few TTS calls)::

    CLAUDE_LIVE_E2E=1 [CORVINTV_LIVE_PYTHON=<console interpreter>] [CORVINTV_LIVE_KEEP=1] \\
        pytest core/console/tests/test_corvintv_native_live_e2e.py -m live -s

``CORVINTV_LIVE_PYTHON`` is the interpreter the console (and so every job) runs with - default: this one.
``CORVINTV_LIVE_KEEP=1`` keeps the temp home and prints the paths for inspection.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(2400),  # boot + provisioning + a real job; the suite default (30 s) is for unit tests
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
        reason="live CorvinTV E2E needs CLAUDE_LIVE_E2E=1 and the signed-in claude CLI",
    ),
]

TENANT = "_default"
TASK = ("Ein sehr kurzes Erklärvideo in zwei Szenen: Warum trinken Raben Wasser aus Pfützen? "
        "Eine Titelszene und eine Szene mit der Antwort, zusammen höchstens 25 Sekunden.")
_COOKIE = "corvin_console_sid"
_MINT = r"""
import json, sys
from corvin_console import auth
rec = auth.create_session(tenant_id=sys.argv[1])
print(json.dumps({"sid": rec.sid, "csrf": auth.derive_csrf_token(rec.csrf_secret, rec.sid)}))
"""
_FORBIDDEN_ENGINE = re.compile(r"ollama|11434|qwen3", re.I)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _real_corvin_home() -> Path:
    return (Path(os.path.expanduser("~")) / ".corvin").resolve()


def _sandbox_env(root: Path, port: int) -> dict:
    home = root / "home"
    corvin_home = root / "corvin_home"
    for d in (home, corvin_home, root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    launcher = home / ".config" / "corvin-launcher" / "config.json"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(json.dumps({"auto_update": False}), encoding="utf-8")
    real_claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path(os.path.expanduser("~")) / ".claude"))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CORVIN_", "CLAUDE_CODE", "CLAUDECODE", "PYTEST_"))}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "CORVIN_HOME": str(corvin_home),
        "CORVIN_CONSOLE_PORT": str(port), "CORVIN_CONSOLE_BASE_URL": f"http://127.0.0.1:{port}",
        "CORVIN_A2A_INGRESS": "off", "CORVIN_TELEMETRY_OPTIN": "false", "CORVIN_GEO_OPT_OUT": "1",
        "CLAUDE_CONFIG_DIR": str(real_claude_dir),  # the CLI keeps its login; nothing else of the real home
        "PYTHONIOENCODING": "utf-8",
    })
    assert _real_corvin_home() not in corvin_home.resolve().parents and corvin_home.resolve() != _real_corvin_home()
    return env


def _probe(video: Path) -> dict:
    """ffprobe's JSON when ffprobe is installed (``FFPROBE`` or PATH), else the plugin's ffmpeg-only probe
    in the same shape (the console's bundled ffmpeg ships without ffprobe)."""
    pinned = os.environ.get("FFPROBE")
    ffprobe = pinned if pinned and Path(pinned).is_file() else shutil.which("ffprobe")
    if ffprobe:
        out = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                             capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
        data["_tool"] = ffprobe
        return data
    # Loaded by FILE under a unique name: "src" is also a top-level package of the CorvinOS checkout,
    # so importing "src.media_probe" resolved to the wrong package. Flat on sys.path, the module's
    # own fallback `import deadline` finds its sibling.
    src = _marketplace_src()
    sys.path.insert(0, str(src))
    # The probe calls `ffmpeg` by name; use the console's own provisioning (bundled imageio-ffmpeg,
    # exposed under that name), exactly what the render job got on its PATH.
    from corvin_console.routes import video_producer_api as _vpa
    _bin = _vpa._ffmpeg_bin_dir()
    _old_path = os.environ.get("PATH", "")
    if _bin is not None and not shutil.which("ffmpeg"):
        os.environ["PATH"] = str(_bin) + os.pathsep + _old_path
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("corvintv_live_media_probe", src / "media_probe.py")
        mp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mp)
        data = mp.probe(video, deadline_s=120, what="live e2e probe")
        data["_tool"] = "media_probe (ffmpeg only)"
        return data
    finally:
        sys.path.remove(str(src))
        os.environ["PATH"] = _old_path


def _marketplace_src() -> Path:
    # <repo>/core/console/tests/<this> -> parents[3] is the CorvinOS checkout; the marketplace is its sibling.
    base = os.environ.get("CORVIN_MARKETPLACE_DIR") or str(Path(__file__).resolve().parents[3].parent / "Corvin-Marketplace")
    return Path(base) / "plugins" / "buildin" / "media" / "corvintv" / "src"


def _vtuple(v: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3])


def test_fresh_home_console_produces_a_real_video_with_native_claude_and_corvinos_tts():
    import httpx

    root = Path(tempfile.mkdtemp(prefix="corvintv-live-")).resolve()
    port = _free_port()
    env = _sandbox_env(root, port)
    py = os.environ.get("CORVINTV_LIVE_PYTHON") or sys.executable
    log_path = root / "console.log"
    base = f"http://127.0.0.1:{port}"
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [py, "-m", "uvicorn", "corvin_console.standalone:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "info", "--loop", "asyncio"],
            env=env, cwd=str(root), stdout=log, stderr=subprocess.STDOUT, creationflags=creationflags,
            start_new_session=os.name != "nt")
    try:
        deadline = time.monotonic() + 240
        while True:
            assert proc.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            try:
                if httpx.get(base + "/", timeout=3, trust_env=False).status_code < 500:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, "console did not come up"
            time.sleep(1)

        minted = subprocess.run([py, "-c", _MINT, TENANT], env=env, cwd=str(root), capture_output=True,
                                text=True, encoding="utf-8", check=True, timeout=180)
        sess = json.loads(minted.stdout.strip().splitlines()[-1])
        client = httpx.Client(base_url=base + "/v1/console", timeout=60, trust_env=False,
                              cookies={_COOKIE: sess["sid"]})
        csrf = {"x-csrf-token": sess["csrf"]}

        # 1. fresh install: boot provisioned CorvinTV >= 1.8.0, installed AND enabled, no operator step
        deadline = time.monotonic() + 600  # the boot thread may have to load the plugin source first
        while True:
            plugins = {p["plugin_id"]: p for p in client.get("/plugins").json().get("plugins", [])}
            tv = plugins.get("video_producer")
            if tv and tv.get("enabled") and client.get("/video/settings").status_code == 200:
                break
            assert time.monotonic() < deadline, f"CorvinTV not installed+enabled: {tv}"
            time.sleep(2)
        assert _vtuple(str(tv["version"])) >= (1, 8, 0), tv
        settings = client.get("/video/settings").json()
        assert settings["tts_engines"] == ["auto"] and settings["effective_tts_engine"] == "auto", settings
        assert settings["tts_chain"]["order"] == ["openai", "edge", "piper"], settings["tts_chain"]
        r = client.put("/video/settings", json={"max_duration_minutes": 1}, headers=csrf)
        assert r.status_code == 200, r.text

        # 2. the console's own job path
        r = client.post("/video/jobs", json={"task": TASK}, headers=csrf)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        deadline = time.monotonic() + 1500
        while True:
            job = client.get(f"/video/jobs/{job_id}").json()
            if job.get("status") in ("complete", "error", "cancelled"):
                break
            assert time.monotonic() < deadline, f"job still {job.get('status')} after 25 min"
            time.sleep(5)
        assert job["status"] == "complete", json.dumps(job, ensure_ascii=False)[:2000]

        # 3. the artifact
        job_dir = next((root / "corvin_home").rglob(f"videos/{job_id}"))
        video = job_dir / "output.mp4"
        assert video.is_file() and video.stat().st_size > 10_000
        probe = _probe(video)
        kinds = {s.get("codec_type") for s in probe["streams"]}
        duration = float(probe["format"]["duration"])
        assert {"video", "audio"} <= kinds and duration > 0, probe

        # 4. the engines that really worked
        report = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "complete"
        assert report["configured"]["storyboard_backend"] == "claude_cli"
        assert str(report["storyboard_llm"]).startswith("claude_cli:"), report["storyboard_llm"]
        assert report["configured"]["tts_chain"] == "corvinos"
        tiers = [s.get("tts_tier") for s in report["scenes"]]
        assert tiers and set(tiers) <= {"openai", "edge", "piper"}, tiers
        assert report["llm_usage"]["calls"] >= 1
        meta = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))["metadata"]
        assert meta["tts_chain"] == "corvinos" and meta["tts_engine"] == "auto"
        assert meta["tts_provider_used"] in {"openai", "edge", "piper", "mixed"}, meta["tts_provider_used"]
        assert str(meta["storyboard_llm"]).startswith("claude_cli:")
        blob = json.dumps(report) + json.dumps(meta)
        assert not _FORBIDDEN_ENGINE.search(blob), "another LLM engine appears in the job record"

        summary = {
            "corvin_home": str(root / "corvin_home"), "plugin_version": tv["version"], "job_id": job_id,
            "video": str(video), "size_bytes": video.stat().st_size, "probe_tool": probe.get("_tool"),
            "duration_s": duration, "streams": sorted(k for k in kinds if k),
            "storyboard_llm": report["storyboard_llm"], "configured": report["configured"],
            "tts_tiers": tiers, "tts_provider_used": meta["tts_provider_used"], "llm_calls": report["llm_usage"]["calls"],
            "scenes": len(report["scenes"]),
        }
        print("\nCORVINTV_LIVE_RESULT " + json.dumps(summary, ensure_ascii=False))
        (root / "live_result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if os.environ.get("CORVINTV_LIVE_KEEP") == "1":
            print(f"\nkept: {root} (console log: {log_path})")
        else:
            shutil.rmtree(root, ignore_errors=True)
    job_lines = [ln for ln in log_text.splitlines() if "video" in ln.lower() or "job_" in ln]
    assert not any(_FORBIDDEN_ENGINE.search(ln) for ln in job_lines), "a video/job log line names another LLM engine"
