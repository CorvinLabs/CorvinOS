"""A stand-in console for the lifecycle-audit E2E (ADR-2247 R2b): loads the plugin the way the host does,
saves one job in a tenant store, starts it through the REAL ProcessJobRunner (job = the [hang] stub, so it
is provably mid-flight), prints READY and idles until it is SIGKILLed - a console killed mid-job.

argv = <plugin src dir> <tenant store base> <job_id>
"""
import asyncio
import importlib
import importlib.util
import sys
import time
from pathlib import Path

src, base, job_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("vp_driver_pkg", src / "__init__.py", submodule_search_locations=[str(src)])
pkg = importlib.util.module_from_spec(spec)
sys.modules["vp_driver_pkg"] = pkg
spec.loader.exec_module(pkg)
models = importlib.import_module("vp_driver_pkg.models")
storage = importlib.import_module("vp_driver_pkg.storage")
runner_mod = importlib.import_module("vp_driver_pkg.async_runner")

task = "Console killed mid-job [hang]"
storage.get_storage(base).save_job(models.VideoJob(id=job_id, task=task, status="pending"))
stub = Path(__file__).resolve().with_name("_vp_lifecycle_stub_job.py")
runner = runner_mod.ProcessJobRunner(entry=[sys.executable, str(stub)])
asyncio.run(runner.start_job(job_id, task, {"storage_base": base, "tts_engine": "openai", "max_duration_minutes": 5}))
print("READY", flush=True)
while True:
    time.sleep(1)
