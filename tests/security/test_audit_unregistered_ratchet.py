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
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")


def test_the_committed_baseline_passes_and_is_non_trivial():
    names = json.loads(BASELINE.read_text("utf-8"))
    assert len(names) > 50, "positive control: the baseline must describe the real scan, not an empty file"
    out = _run()
    assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-500:]
    assert "new: 0" in out.stdout


def test_a_name_missing_from_the_baseline_fails_the_gate(tmp_path):
    names = json.loads(BASELINE.read_text("utf-8"))
    # Drop a name the scan STILL reports. A baseline entry that got registered or lost its emitter
    # is stale (shrink-only file, not yet shrunk); dropping one of those proves nothing and made this
    # test fail on main once `A2A.google_envelope_sent` lost its emitter.
    stale_marker = "NOTE registered now, remove from the baseline (--shrink-baseline): "
    unseen_marker = "remove by hand only after confirming the emitter is gone): "
    stale = {line.split(m, 1)[1].strip()
             for line in _run().stdout.splitlines()
             for m in (stale_marker, unseen_marker) if m in line}
    live = [n for n in names if n not in stale]
    assert live, "positive control: at least one baseline name must still be emitted unregistered"
    dropped = live[0]
    probe = tmp_path / "baseline.json"
    probe.write_text(json.dumps([n for n in names if n != dropped]), encoding="utf-8")
    out = _run("--baseline", str(probe))
    assert out.returncode == 1, out.stdout[-1500:]
    assert f"not in the baseline: {dropped}" in out.stdout


def test_a_registered_name_is_a_note_and_shrink_never_adds(tmp_path):
    names = json.loads(BASELINE.read_text("utf-8"))
    probe = tmp_path / "baseline.json"
    # Review 2026-11-19 round 2 (R2-SEC-1): three kinds of entry.
    #   * a name that HAS a severity entry now ("tool.created")  -> note, shrink drops it;
    #   * a name the scan merely does not see ("zz.not_emitted_anywhere") -> its own note,
    #     and shrink KEEPS it. It used to be reported as "registered now" and dropped,
    #     so a filter that swallowed a real emitter shrank the baseline for an event
    #     that was still written unregistered;
    #   * a real name left out -> never added by shrink.
    probe.write_text(json.dumps(names[1:] + ["zz.not_emitted_anywhere", "tool.created"]),
                     encoding="utf-8")
    out = _run("--baseline", str(probe), "--shrink-baseline")
    assert "registered now, remove from the baseline (--shrink-baseline): tool.created" in out.stdout
    assert ("not seen by the scan any more and NOT registered" in out.stdout
            and "zz.not_emitted_anywhere" in out.stdout)
    assert "registered now, remove from the baseline (--shrink-baseline): zz.not_emitted_anywhere" \
        not in out.stdout
    after = json.loads(probe.read_text("utf-8"))
    assert "tool.created" not in after, "shrink must drop an entry that is registered now"
    assert "zz.not_emitted_anywhere" in after, "shrink dropped a name that was never registered"
    assert names[0] not in after, "shrink must NEVER add an entry that was left out"


def test_init_baseline_refuses_to_overwrite(tmp_path):
    probe = tmp_path / "baseline.json"
    probe.write_text("[]", encoding="utf-8")
    out = _run("--baseline", str(probe), "--init-baseline")
    assert out.returncode == 2
    assert probe.read_text(encoding="utf-8") == "[]"
