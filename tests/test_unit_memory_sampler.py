"""PLAN bg-task P2 item 8: the memory sampler names the holder and never records command lines.

Runs against a real process listed in an injected ``cgroup.procs`` file, so the test cannot read the
operator's own units.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("unit_memory_sampler", REPO / "scripts" / "unit_memory_sampler.py")
sampler = importlib.util.module_from_spec(_spec)
sys.modules["unit_memory_sampler"] = sampler
_spec.loader.exec_module(sampler)  # type: ignore[union-attr]


def _holder() -> subprocess.Popen:
    # holds ~50 MB and carries a recognisable "secret" in its command line
    code = "x = bytearray(50 * 1024 * 1024); import time; time.sleep(60)  # SECRET-IN-ARGV"
    return subprocess.Popen([sys.executable, "-c", code])


def test_sample_names_the_holder_by_executable_only():
    proc = _holder()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            procs = Path(tmp) / "cgroup.procs"
            procs.write_text(f"{proc.pid}\n", encoding="utf-8")
            import time
            time.sleep(0.5)                       # let the allocation land
            s = sampler.sample("unit-under-test", procs)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert s["processes"] == 1
    assert s["total_rss_gb"] > 0
    assert s["top"][0]["pid"] == proc.pid
    blob = json.dumps(s)
    assert "SECRET-IN-ARGV" not in blob           # command lines are never recorded


def test_run_writes_only_above_the_threshold_and_a_baseline():
    proc = _holder()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            procs = Path(tmp) / "cgroup.procs"
            procs.write_text(f"{proc.pid}\n", encoding="utf-8")
            out = Path(tmp) / "samples.jsonl"
            import time
            time.sleep(0.5)
            # above the threshold: nothing for the holder (threshold 1000 GB), baseline off (huge interval)
            sampler.run(["u"], out, 0.1, 1000.0, 10**9, True, {"u": procs})
            first = out.read_text(encoding="utf-8") if out.exists() else ""
            # threshold 0: the holder is recorded as "high"
            sampler.run(["u"], out, 0.1, 0.0, 10**9, True, {"u": procs})
            lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert first.count("\n") == 1                 # the very first call writes the baseline line only
    assert json.loads(first)["kind"] == "baseline"
    assert lines[-1]["kind"] == "high"
