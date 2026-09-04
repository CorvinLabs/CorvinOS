#!/usr/bin/env python3
"""E2E wiring proof for ADR-0596/0597 (e2e-wiring-proof gate).

Phase 1 (reachability): `classify_speech_type` is reached from the real CLI
entry the bridge spawns — main() → summarize() → classify_speech_type. Proven by
a grep-style source assertion + by actually running the CLI.

Phase 2 (functional): drive the REAL subprocess boundary (not a direct import).
Without a live `claude` CLI / Ollama the run deterministically exercises the
ADR-0597 degrade ladder — that proves option-safety end-to-end through the CLI.
The LLM-prompt-shape assertions (ADR-0596 outcome-first bundling) need a live
backend and are skipped-with-reason when none is authenticated (infeasibility
exception named, not silent).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SUMMARIZE = HERE / "summarize.py"
sys.path.insert(0, str(HERE))
import summarize  # noqa: E402


DECISION = ("Für das Datenbank-Setup gibt es drei Wege.\n"
            "a) Postgres: robust, aber mehr Betrieb\n"
            "b) SQLite: einfach, aber Single-Node\n"
            "c) Managed Cloud: teuer, aber wartungsfrei\n"
            "Welche Variante willst du?")

REPORT = ("Erledigt: Ich habe den Login-Flow neu gebaut. Konkret: die Session "
          "wird jetzt serverseitig gehalten, das Race beim Token-Refresh ist "
          "weg, und die drei Flaky-Tests sind grün. Damit funktioniert der "
          "parallele Login jetzt zuverlässig.")


def _run_cli(text: str, lang: str = "de", max_chars: int = 400,
             backend: str = "structural") -> tuple[str, str]:
    """Drive summarize.py as the bridge does — a real subprocess over stdin.

    Default backend='structural' forces the deterministic ADR-0597 degrade
    ladder (no LLM), where option-safety is a hard guarantee — that is what an
    E2E can assert reliably. backend='auto' exercises the live LLM path (best
    effort, model-dependent) for the outcome-shape test only. Full env is passed
    so PATH/HOME are present.
    """
    env = os.environ.copy()
    env["VOICE_SUMMARIZE_BACKEND"] = backend
    proc = subprocess.run(
        [sys.executable, str(SUMMARIZE), "--lang", lang,
         "--max-chars", str(max_chars)],
        input=text, capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, f"CLI exit {proc.returncode}: {proc.stderr[-400:]}"
    return proc.stdout.strip(), proc.stderr


def _has_live_backend() -> bool:
    try:
        return bool(summarize._claude_authenticated())
    except Exception:
        return False


# --- Phase 1: reachability ---------------------------------------------------

def test_classifier_reachable_from_cli_entry() -> None:
    src = SUMMARIZE.read_text(encoding="utf-8")
    # Real, non-test call site: summarize() (reached from main()) classifies.
    assert "classify_speech_type(text)" in src
    assert "def summarize(" in src and "print(summarize(" in src


def test_cli_emits_type_diagnostic() -> None:
    _out, stderr = _run_cli(DECISION, max_chars=400)
    # The new diagnostic line names the classified type (content-free).
    assert "type=decision" in stderr


# --- Phase 2: functional (always-run, deterministic degrade path) -----------

def test_decision_keeps_all_options_through_cli_degrade() -> None:
    """Option-safety end-to-end through the REAL CLI subprocess on the
    deterministic ADR-0597 degrade path: every option and the closing question
    must survive. (The live-LLM path relies on the AUSWAHL prompt rule, which a
    weak local model may not obey — that is prompt-best-effort, not a hard E2E
    guarantee, so it is not asserted here.)"""
    out, _ = _run_cli(DECISION, max_chars=120, backend="structural")
    low = out.lower()
    for cue in ("postgres", "sqlite", "cloud"):
        assert cue in low, f"option {cue!r} missing from spoken output: {out!r}"
    assert out.rstrip().endswith("?") or "variante" in low


def test_report_output_is_bounded_and_nonempty() -> None:
    # Budget WELL below the source length so bounding is genuinely exercised.
    out, _ = _run_cli(REPORT, max_chars=120, backend="structural")
    assert out
    # Degrade is bounded and strictly shorter than the raw answer.
    assert len(out) < len(REPORT)
    assert len(out) <= 120 + 40


# --- Phase 2: LLM-prompt-shape (skip-with-reason without a backend) ---------

@pytest.mark.skipif(not _has_live_backend(),
                    reason="ADR-0596 outcome-first bundling needs a live LLM "
                           "backend (claude CLI / Ollama); degrade path cannot "
                           "exhibit prompt-driven bundling — infeasible in CI.")
def test_report_leads_with_outcome_when_llm_present() -> None:
    out, _ = _run_cli(REPORT, max_chars=400, backend="auto")
    low = out.lower()
    # A report should surface the outcome/effect, not read as a bare step list.
    assert any(w in low for w in ("login", "zuverlässig", "funktioniert",
                                  "session", "race"))
