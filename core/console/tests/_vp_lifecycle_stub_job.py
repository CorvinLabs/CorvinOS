"""Stand-in job process for the host lifecycle-audit E2E (ADR-2247 R2b): argv = <job_id> <config.json>.

Started by the plugin's REAL ProcessJobRunner in place of ``job_main.py`` (a real job calls an LLM, a TTS
provider and ffmpeg). It writes its record through the plugin's own storage (the single write path) and
its report through the plugin's own ``ProductionReport``. Behaviour is chosen by a marker in the task:

  [complete]  finish complete, clean report
  [degraded]  finish complete, report with one web->Pillow degradation
  [pair]      before finishing, wait until two [pair] jobs have started (proves they overlapped)
  [hang]      mark the job running, then sleep (cancel / console-kill target)
  [die]       exit 7 without a terminal record (the reaper closes it child_exit)
"""
import importlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

job_id, cfg = sys.argv[1], Path(sys.argv[2])
payload = json.loads(cfg.read_text())
cfg.unlink()  # like job_main: the task file does not outlive its read
task = payload["task"]
base = Path(payload["config"]["storage_base"])

here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
src = Path(payload["src_dir"])
spec = importlib.util.spec_from_file_location("vp_stub_pkg", src / "__init__.py", submodule_search_locations=[str(src)])
pkg = importlib.util.module_from_spec(spec)
sys.modules["vp_stub_pkg"] = pkg
spec.loader.exec_module(pkg)
storage_mod = importlib.import_module("vp_stub_pkg.storage")
report_mod = importlib.import_module("vp_stub_pkg.report")
store = storage_mod.get_storage(str(base), mark_interrupted=False)


def advance(status, **fields):
    def _m(j):
        j.status = status
        for k, v in fields.items():
            setattr(j, k, v)
    store.advance_job(job_id, _m)


advance("skills_running", started_at=datetime.now(), percent=10)
marks = base / "stub_marks"
marks.mkdir(exist_ok=True)
(marks / job_id).write_text("started")

if "[die]" in task:
    sys.exit(7)
if "[hang]" in task:
    time.sleep(600)
    sys.exit(0)
if "[pair]" in task:
    end = time.monotonic() + 20
    while time.monotonic() < end and len([p for p in marks.iterdir() if (p.read_text() == "started")]) < 2:
        time.sleep(0.05)
    time.sleep(0.3)  # both are now running at the same time

rep = report_mod.ProductionReport(job_id, configured_tts="openai", storyboard_backend="claude_cli")
rep.begin("scenes")
for i in (1, 2):
    rep.scene(i, tts_tier="openai", audio_s=4.0, renderer="web", template="title")
if "[degraded]" in task:
    rep.scene(3, tts_tier="openai", audio_s=4.0, renderer="pillow", template=None)
    rep.note_degradation("web_to_pillow", scene=3, reason="render_timeout")
rep.finish("complete")
rep.write(store.videos_dir / job_id, forbidden_texts=[task])
advance("complete", completed_at=datetime.now(), percent=100)
