"""The audit completeness gate fails on a NEW event name emitted without a severity entry (PLAN-0965 F-11).

Runs the real script as a subprocess against the real repo. The baseline is shrink-only: today's 202 names pass,
a name missing from the baseline fails (positive control), a name that is registered now is only a note, and the
shrink option never adds an entry.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_audit_event_completeness.py"
BASELINE = REPO / "scripts" / "audit_unregistered_baseline.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=300)


def test_the_committed_baseline_passes_and_is_non_trivial():
    names = json.loads(BASELINE.read_text("utf-8"))
    assert len(names) > 50, "positive control: the baseline must describe the real scan, not an empty file"
    out = _run()
    assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-500:]
    assert "new: 0" in out.stdout


def test_a_name_missing_from_the_baseline_fails_the_gate(tmp_path):
    names = json.loads(BASELINE.read_text("utf-8"))
    dropped = names[0]
    probe = tmp_path / "baseline.json"
    probe.write_text(json.dumps(names[1:]))
    out = _run("--baseline", str(probe))
    assert out.returncode == 1, out.stdout[-1500:]
    assert f"not in the baseline: {dropped}" in out.stdout


def test_a_registered_name_is_a_note_and_shrink_never_adds(tmp_path):
    names = json.loads(BASELINE.read_text("utf-8"))
    probe = tmp_path / "baseline.json"
    # one made-up name (stands for "registered since") and one real name left out
    probe.write_text(json.dumps(names[1:] + ["zz.not_emitted_anywhere"]))
    out = _run("--baseline", str(probe), "--shrink-baseline")
    assert "registered now, remove from the baseline (--shrink-baseline): zz.not_emitted_anywhere" in out.stdout
    after = json.loads(probe.read_text("utf-8"))
    assert "zz.not_emitted_anywhere" not in after, "shrink must drop the stale entry"
    assert names[0] not in after, "shrink must NEVER add an entry that was left out"


def test_init_baseline_refuses_to_overwrite(tmp_path):
    probe = tmp_path / "baseline.json"
    probe.write_text("[]")
    out = _run("--baseline", str(probe), "--init-baseline")
    assert out.returncode == 2
    assert probe.read_text() == "[]"
