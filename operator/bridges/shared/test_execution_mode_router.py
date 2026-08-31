#!/usr/bin/env python3
"""Tests for execution_mode_router.py (ADR-0127 M1)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_mode_router as emr  # noqa: E402


# ── single (fail-safe default) ────────────────────────────────────────────

@pytest.mark.parametrize("prompt", [
    "",
    "Was ist die Hauptstadt von Frankreich?",
    "Schreib mir eine kurze E-Mail an das Team.",
    "Wie spät ist es?",
    "Erklär mir, wie der Audit-Chain funktioniert.",
])
def test_plain_prompts_route_to_single(prompt):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_SINGLE


@pytest.mark.parametrize("prompt", [
    # Soft verbs without a co-signal must NOT trigger a richer mode.
    "optimiere meinen Text",
    "optimize my code a bit",
    "minimiere die Risiken im Plan",
    "maximiere die Lesbarkeit",
    "find the best name for this variable",
    "kannst du das kurz beobachten",
    "überwache den Fortschritt kurz",
    "I keep forgetting things",
    "der Sweep im Diagramm sieht komisch aus",
    "erklär mir den Loss bei neuronalen Netzen",
])
def test_soft_verbs_without_cosignal_stay_single(prompt):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_SINGLE, r


@pytest.mark.parametrize("bad", [None, 123, b"bytes", 4.5, ["list"], {"d": 1}])
def test_non_str_input_never_raises(bad):
    r = emr.classify(bad)  # type: ignore[arg-type]
    assert r["mode"] == emr.MODE_SINGLE
    assert isinstance(r, dict)


def test_fallback_confidence_not_inverted():
    # A rejected richer signal must NOT make single's confidence drop to 0.
    r = emr.classify("optimiere meinen Text")  # soft compute 0.35 < floor
    assert r["mode"] == emr.MODE_SINGLE
    assert r["confidence"] == 0.5
    assert r["params"].get("rejected_mode") == emr.MODE_COMPUTE


# ── loop ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,expect_interval", [
    ("Beobachte den Deploy alle 30 Minuten", 1800),
    ("check the build every 5 minutes", 300),
    ("Überwache die CI alle 2 Stunden", 3600),          # clamped to max
    ("ping the queue every 10 seconds", 60),            # clamped to min
])
def test_loop_with_explicit_interval(prompt, expect_interval):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_LOOP, r
    assert r["params"]["interval_s"] == expect_interval
    assert r["params"]["explicit_recurrence"] is True


@pytest.mark.parametrize("prompt", [
    "Arbeite die offenen Issues ab, bis alle erledigt sind",
    "keep retrying until the test passes",
    "überwache das Log dauerhaft",
])
def test_loop_dynamic_no_interval(prompt):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_LOOP, r
    assert r["params"]["interval_s"] is None
    assert r["params"]["explicit_recurrence"] is False


# ── workflow ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt", [
    "Auditiere gründlich das ganze Repository auf Sicherheitslücken",
    "compare several approaches and pick the best architecture",
    "Migriere jede Datei im ganzen Ordner auf das neue API",
    "fan this out across multiple agents",
    "review the entire codebase thoroughly",
])
def test_workflow_signals(prompt):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_WORKFLOW, r


# ── compute ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,strat", [
    ("Optimiere die Hyperparameter per Bayesian search", "bayesian"),
    ("Finde die beste Konfiguration über alle Parameter-Werte", "grid"),
    ("Mach einen parameter sweep und minimiere die loss metric", "grid"),
    ("run a random search to find the best threshold for the query", "random"),
])
def test_compute_signals(prompt, strat):
    r = emr.classify(prompt)
    assert r["mode"] == emr.MODE_COMPUTE, r
    assert r["params"]["strategy_hint"] == strat


# ── tie-break: stronger/costlier mode wins on equal score ─────────────────

def test_compute_beats_loop_on_overlap():
    # "optimiere ... regelmäßig" hits both; compute is the stronger claim.
    r = emr.classify("Optimiere die Parameter regelmäßig per grid search")
    assert r["mode"] in (emr.MODE_COMPUTE, emr.MODE_LOOP)
    # compute weight (0.55+0.7) should dominate the single loop hit.
    assert r["mode"] == emr.MODE_COMPUTE, r


# ── confidence floor ──────────────────────────────────────────────────────

def test_weak_signal_falls_back_to_single():
    # A single low-weight token below the 0.5 floor → single.
    r = emr.classify("vielleicht ab und zu mal schauen", min_confidence=0.9)
    assert r["mode"] == emr.MODE_SINGLE


# ── EMR_FAKE test hook ────────────────────────────────────────────────────

def test_emr_fake_override(monkeypatch):
    monkeypatch.setenv("EMR_FAKE", "1")
    monkeypatch.setenv("EMR_FAKE_RESULT", '{"mode":"workflow","confidence":0.9}')
    r = emr.classify("anything at all")
    assert r["mode"] == "workflow"
    assert r["confidence"] == 0.9


def test_emr_fake_default(monkeypatch):
    monkeypatch.setenv("EMR_FAKE", "1")
    monkeypatch.delenv("EMR_FAKE_RESULT", raising=False)
    r = emr.classify("optimiere alles per bayesian sweep")
    assert r["mode"] == emr.MODE_SINGLE  # fake overrides the heuristic


# ── result shape contract ─────────────────────────────────────────────────

def test_result_shape():
    r = emr.classify("optimiere die parameter per bayesian search")
    assert set(r.keys()) == {"mode", "confidence", "why", "params"}
    assert r["mode"] in emr.ALL_MODES
    assert 0.0 <= r["confidence"] <= 1.0
    assert isinstance(r["params"], dict)


# ── no anthropic import (CI AST lint mirror) ──────────────────────────────

def test_no_anthropic_import():
    """AST walk — the module must not import the anthropic SDK (L25 rule)."""
    import ast
    tree = ast.parse(Path(emr.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name != "anthropic" and not a.name.startswith("anthropic.")
                       for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "anthropic" and not (node.module or "").startswith("anthropic.")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
