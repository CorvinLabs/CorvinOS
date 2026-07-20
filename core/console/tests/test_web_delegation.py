"""ADR-0114 — web-chat delegation path: triage, flag, budget, spec builder.

Pure-function tests; no subprocess, no network, no ACS spawn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from corvin_console import chat_runtime as cr  # noqa: E402


# ── triage ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "/delegate male ein bild von einem hund",
    "/DELEGATE auch case-insensitiv",
    "Analysiere alle Spieltage der Bundesliga und erstelle danach eine Tabelle "
    "mit den wichtigsten Statistiken pro Verein.",
    "Create a report comparing three frameworks and then summarize the steps.",
    "x" * 400,  # long prompts are substantive by definition
])
def test_triage_delegates_substantive(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True


@pytest.mark.parametrize("prompt", [
    "hallo",
    "wie spät ist es?",
    "danke!",
    "was ist 2+2",
    "erkläre kurz",  # verb-less smalltalk stays direct
])
def test_triage_keeps_trivial_direct(prompt: str) -> None:
    assert cr._should_delegate(prompt) is False


# ── ACS-suitability rework (2026-07-20): coding → direct, fan-out → ACS ──────


@pytest.mark.parametrize("prompt", [
    # Strong verbs that used to force the ACS fan-out now stay direct when the
    # task is coding-shaped: coding is sequential, needs the shared session
    # workspace, and each ACS turn burns one compute_units_per_day.
    "Behebe den Bug in der Login-Funktion",
    "Fix the failing test in test_auth.py",
    "Debugge den Traceback aus dem letzten Lauf",
    "Refactor the authentication module and add unit tests",
    "Implementiere die neue Export-Funktion in server.py und schreibe Tests dafür",
    "Review den Code in chat_runtime.py und behebe die Fehler",
    # Long coding prompts stay direct too — length alone must not override
    # the coding shape (pre-rework, ≥400 chars force-delegated everything).
    "Fix the bug in the parser module: " + "der Fehler tritt im Code auf " * 20,
])
def test_triage_routes_coding_to_direct_claude_code(prompt: str) -> None:
    """Coding-shaped work takes the direct OS-turn (Claude Code's own
    Task-tool sub-delegation), NOT the ACS manager/worker fan-out."""
    assert cr._should_delegate(prompt) is False


@pytest.mark.parametrize("prompt", [
    # Explicitly parallel / fan-out-shaped work is what ACS is FOR — even when
    # code is involved (the fan-out marker wins over the coding shape).
    "Review den Code aus Security-, Performance- und Style-Perspektive parallel",
    "Analysiere die Module unabhängig voneinander mit mehreren Workern",
    "Recherchiere aus mehreren Quellen die Marktlage und vergleiche danach die Anbieter",
    "Compare the three frameworks independently and then collect the results",
    # Explicit user override beats every shape — /delegate always fans out.
    "/delegate fix the bug in server.py",
])
def test_triage_routes_fanout_to_acs(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Wie vergleiche ich zwei Dateien?",     # fan-out word, but smalltalk shape
    "Vergleiche kurz A und B",              # short compare without multi-step
])
def test_triage_fanout_words_alone_do_not_delegate(prompt: str) -> None:
    assert cr._should_delegate(prompt) is False


# ── tenant flag + budget ──────────────────────────────────────────────


def _write_tenant_yaml(home: Path, tenant: str, body: str) -> None:
    p = home / "tenants" / tenant / "global"
    p.mkdir(parents=True, exist_ok=True)
    (p / "tenant.corvin.yaml").write_text(body, encoding="utf-8")


def test_delegation_flag_default_deny(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    # No tenant file at all → deny
    assert cr._delegation_enabled("_default") is False
    # File without the key → deny
    _write_tenant_yaml(tmp_path, "_default", "spec:\n  compute:\n    enabled: true\n")
    assert cr._delegation_enabled("_default") is False


def test_delegation_flag_opt_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    _write_tenant_yaml(
        tmp_path, "_default",
        "spec:\n  web_chat:\n    delegation_enabled: true\n",
    )
    assert cr._delegation_enabled("_default") is True


def test_delegation_budget_defaults_and_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr._forge_paths, "corvin_home", lambda: tmp_path)
    assert cr._delegation_budget("_default") == cr._DELEGATION_BUDGET_DEFAULTS
    _write_tenant_yaml(
        tmp_path, "_default",
        "spec:\n  web_chat:\n    budget:\n      max_total_workers: 8\n"
        "      max_wall_time: -5\n      bogus: 99\n",
    )
    b = cr._delegation_budget("_default")
    assert b["max_total_workers"] == 8
    assert b["max_wall_time"] == cr._DELEGATION_BUDGET_DEFAULTS["max_wall_time"]
    assert "bogus" not in b


# ── workflow spec builder ─────────────────────────────────────────────


def test_delegation_spec_is_valid_awp() -> None:
    spec = cr._build_delegation_spec("do the thing", cr._DELEGATION_BUDGET_DEFAULTS)
    assert spec["awp"] == "1.0.0"
    assert spec["workflow"]["description"] == "do the thing"
    assert spec["orchestration"]["engine"] == "delegation_loop"
    assert spec["orchestration"]["delegation_loop"]["budget"] == cr._DELEGATION_BUDGET_DEFAULTS
    # The budget dict must be a copy — callers must not share mutable state.
    spec["orchestration"]["delegation_loop"]["budget"]["max_loops"] = 99
    assert cr._DELEGATION_BUDGET_DEFAULTS["max_loops"] != 99


def test_delegation_spec_passes_acs_validator() -> None:
    shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    sys.path.insert(0, str(shared))
    try:
        from acs_validator import validate_workflow_dict  # type: ignore
    except ImportError:
        pytest.skip("acs_validator not importable in this environment")
    spec = cr._build_delegation_spec("two-step task", cr._DELEGATION_BUDGET_DEFAULTS)
    result = validate_workflow_dict(spec)
    blocking = [i for i in result.issues if i.severity.upper() in ("ERROR", "CRITICAL")]
    assert not blocking, f"ACS validator rejected the web delegation spec: {blocking}"


# ── ACS-1: delegation budget siblings must stay at sane bounds ────────────────

def test_acs1_budget_defaults_are_sane() -> None:
    """The a47c6d3 blanket 100x scale-up (max_total_workers=400,
    max_wall_time=360000, max_loops=500, max_worker_turns=10000) was a
    quota-defeat: one metered compute unit authorized 400 workers for 100 h.

    Maintainer decision 2026-07-20 (supersedes the 2026-07-16 relaxation):
    defaults sit AT the validation ceilings so an unconfigured budget never
    stops a task mid-run. "Sane" therefore now means: never ABOVE the
    R32/R35/R36 guard line — the ceilings themselves (64 workers / 24 h /
    depth ≤ 10) are the invariant, and anything past them is the inflation
    class this test was written against. The exhaustive default==ceiling pins
    live in test_delegation_budget_defaults.py.
    """
    d = cr._DELEGATION_BUDGET_DEFAULTS
    worker_hours = d["max_total_workers"] * (d["max_wall_time"] / 3600)
    assert worker_hours <= 64 * 24, (
        f"{worker_hours} worker-hours per metered compute unit exceeds the "
        "R35/R36 guard line (64 workers x 24 h) — a47c6d3 inflation class"
    )
    assert 1 <= d["max_total_workers"] <= 64, d["max_total_workers"]
    assert d["max_wall_time"] <= 86400, d["max_wall_time"]
    assert d["timeout_seconds"] <= 86400, d["timeout_seconds"]
    assert 1 <= d["max_loops"] <= 100, d["max_loops"]
    assert 1 <= d["max_worker_turns"] <= 5000, d["max_worker_turns"]
    assert 1 <= d["max_depth"] <= 10, d["max_depth"]


def test_acs1_inflated_budget_would_be_rejected_by_validator() -> None:
    """Backstop: had the inflated siblings NOT been reverted, the acs_validator
    R35/R36 ceilings would now reject the spec loudly."""
    shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    sys.path.insert(0, str(shared))
    try:
        from acs_validator import validate_workflow_dict  # type: ignore
    except ImportError:
        pytest.skip("acs_validator not importable in this environment")
    inflated = dict(cr._DELEGATION_BUDGET_DEFAULTS)
    inflated["max_total_workers"] = 400
    inflated["max_wall_time"] = 360000
    spec = cr._build_delegation_spec("two-step task", inflated)
    result = validate_workflow_dict(spec)
    rule_ids = {i.rule_id for i in result.errors}
    assert "R35" in rule_ids and "R36" in rule_ids, rule_ids
