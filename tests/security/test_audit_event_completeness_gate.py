"""scripts/verify_audit_event_completeness.py measures the imported registry.

Round 2 of the 2026-09-27 adversarial review found the old gate regex-parsed
``security_events.py`` and mis-counted every entry that was not a one-line
literal. These tests pin the replacement: it reads the real dicts, it fails on
an emitted event without an allowlist (positive control — proves the emitter
scan actually reaches a real call site), it does not count a reader's filter
set as an emission, and the repository passes it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_audit_event_completeness.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_audit_event_completeness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repository_passes_the_gate_in_a_bare_interpreter(tmp_path):
    # Same shape as the CI job: plain python3, no PYTHONPATH, no venv deps.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "CORVIN_HOME": str(tmp_path / "corvin")},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "emitted, unregistered : 0" in proc.stdout
    assert "allowlisted, no severity: 0" in proc.stdout


def test_counts_are_the_imported_dicts():
    # Count in a FRESH interpreter: in this process other tests may already
    # have added runtime allowlists (register_event_allowlist), which the
    # static count the script reports rightly does not include.
    probe = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('v', {str(SCRIPT)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "se=m.load_registry();print(len(se.EVENT_SEVERITY), len(se._EVENT_ALLOWLIST))"
    )
    counts = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
                            capture_output=True, text=True, timeout=120).stdout.split()
    n_sev, n_allow = counts[-2], counts[-1]
    out = subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO,
                         capture_output=True, text=True, timeout=120).stdout
    assert f"EVENT_SEVERITY entries : {n_sev}" in out
    assert f"_EVENT_ALLOWLIST entries: {n_allow}" in out


def test_emitter_scan_finds_a_real_emitter():
    # Positive control: a known production emitter (model_selector writes
    # os_model.selected via a write call) must be found, or a green gate proves
    # nothing about the scan's reach.
    mod = _load()
    found = mod.find_emitters({"os_model.selected"})
    assert "os_model.selected" in found, found
    assert any("model_selector" in f for f in found["os_model.selected"])


def test_reader_filter_sets_are_not_emissions():
    # chain_dual_track lists os_turn.error in a frozenset it filters on and
    # maturity_live counts it with counts.prefix(...); neither writes it.
    mod = _load()
    assert "os_turn.error" not in mod.find_emitters({"os_turn.error"})


def test_gate_fails_on_emitted_event_without_allowlist(monkeypatch, capsys):
    mod = _load()
    se = mod.load_registry()
    monkeypatch.delitem(se._EVENT_ALLOWLIST, "os_model.selected")
    assert mod.main([]) == 1
    assert "FAIL emitted without allowlist: os_model.selected" in capsys.readouterr().out


def test_gate_fails_on_allowlist_without_severity(monkeypatch, capsys):
    mod = _load()
    se = mod.load_registry()
    monkeypatch.delitem(se.EVENT_SEVERITY, "os_model.selected")
    assert mod.main([]) == 1
    assert "FAIL allowlisted without severity: os_model.selected" in capsys.readouterr().out


def test_floor_by_design_entries_are_not_allowlisted():
    mod = _load()
    se = mod.load_registry()
    for event in mod.FLOOR_BY_DESIGN:
        assert event in se.EVENT_SEVERITY, event
        assert event not in se._EVENT_ALLOWLIST, (
            f"{event} is allowlisted — drop it from FLOOR_BY_DESIGN"
        )
