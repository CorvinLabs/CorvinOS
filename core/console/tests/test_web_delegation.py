"""ADR-0114 — web-chat delegation path: triage, flag, budget, spec builder.

Pure-function tests; no subprocess, no network, no ACS spawn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"))

from corvin_console import chat_runtime as cr  # noqa: E402
from acs_classify import heuristic_classify as _hc  # noqa: E402


# ── triage ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "/delegate male ein bild von einem hund",
    "/DELEGATE auch case-insensitiv",
    # NB: the old "Bundesliga-Tabelle mit Statistiken" case moved to the
    # ADR-0203 ladder tests below — data aggregation is a COMPUTE shape
    # (L25 deterministic compute), not an LLM worker fan-out.
    "Analysiere alle Spieltage der Bundesliga und erstelle danach eine "
    "Übersicht der spannendsten Spiele pro Verein.",
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


# ── ADR-0203 priority ladder: LOOP/GOAL/COMPUTE/DELEGATE shapes never fan out ─


@pytest.mark.parametrize("prompt", [
    # LOOP shape — a recurring task belongs to the scheduler / loop iteration;
    # burning one compute unit per fire in the ACS fan-out would be the worst
    # possible routing. The research wording must NOT win over the recurrence.
    "Recherchiere jede Stunde die neuesten Nachrichten aus mehreren Quellen "
    "und vergleiche danach die Schlagzeilen",
    "Überwache den Server regelmäßig und erstelle danach einen Bericht über "
    "die Ausfälle mit mehreren Perspektiven",
    # GOAL shape — persistent objective, not a one-shot fan-out.
    "Setze als dauerhaftes Ziel: verbessere die Testabdeckung des Projekts "
    "Schritt für Schritt und vergleiche mehrere Ansätze",
    # COMPUTE shape — deterministic data processing (L25), not LLM workers.
    "Analysiere die CSV mit den Verkaufszahlen und erstelle mehrere Charts "
    "und dann eine Zusammenfassung der Statistik",
    # DELEGATE shape — explicit engine wish routes to corvin_delegate.
    "Frag Hermes nach einer Zusammenfassung der Logs und erstelle danach "
    "einen Bericht über mehrere Kandidaten",
])
def test_triage_ladder_non_fanout_primitives_stay_direct(prompt: str) -> None:
    """ACS-X shapes LOOP/GOAL/COMPUTE/DELEGATE are checked BEFORE the fan-out
    shape — their correct mechanism is never the quota-burning ACS fan-out."""
    assert cr._should_delegate(prompt) is False


def test_triage_ladder_fails_open_without_acs_classify(monkeypatch) -> None:
    """When the shared classifier is unavailable, the triage must fall back to
    its own regex rules — never crash, never change the fan-out contract."""
    monkeypatch.setattr(cr, "_acs_x_blueprint", lambda p: None)
    assert cr._should_delegate(
        "Recherchiere aus mehreren Quellen die Marktlage und vergleiche "
        "danach die Anbieter") is True
    assert cr._should_delegate("hallo") is False


# ── ADR-0203 bridge parity: console OS-turn carries the <acs_directive> ──────


def test_console_directive_block_for_loop_task() -> None:
    block = cr._acs_directive_block("Überwache den Ordner jede Stunde auf neue Dateien")
    assert "<acs_directive" in block
    assert 'primitive="LOOP"' in block


def test_console_directive_block_empty_for_direct_and_blank() -> None:
    assert cr._acs_directive_block("wie spät ist es?") == ""
    assert cr._acs_directive_block("") == ""
    assert cr._acs_directive_block("   ") == ""


def test_console_directive_suppresses_workflow_on_direct_turn() -> None:
    """Review F9: this block is injected ONLY on the direct (un-metered) turn.
    A WORKFLOW directive ("Use the Workflow tool ...") would steer that turn
    back into quota-charging compute — contradictory. It must be suppressed."""
    wf_prompt = "Mach einen iterativen Code-Review über das ganze Repo und behebe die Fehler"
    # sanity: the classifier really sees this as WORKFLOW, and the turn is
    # routed DIRECT (coding tokens repo/code) — the exact F9 contradiction.
    bp = _hc(wf_prompt)
    assert bp.primitive == "WORKFLOW", bp.primitive
    assert cr._should_delegate(wf_prompt) is False
    assert cr._acs_directive_block(wf_prompt) == ""


# ── ADR-0203 review fixes: routing-precision regressions (F1/F2/F4/F5) ───────


@pytest.mark.parametrize("prompt", [
    # F1 — low-weight recurrence words (stündlich 0.65, täglich 0.60) are below
    # the old 0.70 gate yet must still route direct (scheduler, not ACS).
    "Recherchiere stündlich die Preise der Wettbewerber und vergleiche mehrere Anbieter",
    "Vergleiche täglich mehrere Nachrichtenquellen und fasse sie danach zusammen",
    # F5 — crash/freeze coding vocabulary without a code token.
    "Behebe den Absturz der App beim Start",
    "Fix the crash when opening the settings page",
    "Die Anwendung hängt sich beim Speichern auf, bitte beheben",
])
def test_triage_precision_stays_direct(prompt: str) -> None:
    assert cr._should_delegate(prompt) is False


@pytest.mark.parametrize("prompt", [
    # F2/F3/F4 — explicit parallelism outranks every classifier collision.
    "Vergleiche parallel mit mehreren Workern die aktuellen Apple Watch Modelle",
    "Vergleiche parallel mit mehreren Workern die besten 4K Monitor Modelle",
    "Vergleiche parallel mit mehreren Workern die Versandkosten mit Hermes und DHL",
    "Delegiere die Recherche an mehrere parallele Worker und sammle die Ergebnisse",
    "Analysiere die drei CSV-Dateien parallel mit mehreren Workern und erstelle Charts",
    # F4 — inflected German: "parallele Recherchen" (neither bare form matches).
    "Führe drei parallele Recherchen zu Strompreisen durch und fasse sie zusammen",
    # F2 — per-item fan-out the unbounded jede-regex mis-classified as LOOP;
    # with the span bounded it is no longer LOOP and reaches the fan-out gate.
    "Recherchiere für jeden der fünf Anbieter die Lieferzeit in Minuten und vergleiche sie danach",
])
def test_triage_explicit_parallel_and_fanout_take_acs(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True


def test_triage_bare_delegate_without_engine_is_not_forced_direct() -> None:
    """F2: a bare "delegiere" with no named engine is ambiguous — it must NOT
    be routed direct by rule 2 (only a NAMED engine is unambiguous intent)."""
    # A named engine → direct (rule 2 DELEGATE branch fires).
    assert cr._should_delegate("Frag Hermes nach einer kurzen Zusammenfassung") is False
    # Bare "delegiere" + explicit workers → explicit-parallel wins → ACS.
    assert cr._should_delegate(
        "Delegiere die Aufgabe an mehrere parallele Worker") is True


def test_triage_loosening_gate_low_confidence_does_not_steal_fanout(monkeypatch) -> None:
    """Review test-F4: a spurious LOW-confidence non-fan-out classification must
    not hijack a genuinely fan-out-shaped task away from ACS. Below the 0.50
    render floor, rule 2 must not fire."""
    from acs_classify import ACSBlueprint  # type: ignore
    monkeypatch.setattr(
        cr, "_acs_x_blueprint",
        lambda p: ACSBlueprint(primitive="COMPUTE", confidence=0.30, path="heuristic"))
    # A clearly fan-out task must still reach ACS despite the weak COMPUTE guess.
    assert cr._should_delegate(
        "Recherchiere aus mehreren Quellen die Marktlage und vergleiche "
        "danach die drei größten Anbieter") is True


# ── D6 (adversarial review 2026-07-20): rule 1b must not hijack coding/LOOP ──


@pytest.mark.parametrize("prompt", [
    # Incidental "worker"/"gleichzeitig" vocabulary inside a coding prompt
    # used to fire rule 1b (_EXPLICIT_PARALLEL_RE) ABOVE the coding triage and
    # burn the daily compute unit on the ACS fan-out (finding D6).
    "Debug why the celery worker crashes during startup",
    "Fix den Bug: der Background-Worker hängt sich beim Start auf",
    "Das Programm stürzt ab, wenn zwei Nutzer gleichzeitig speichern — fix das",
])
def test_triage_parallel_words_never_hijack_coding(prompt: str) -> None:
    """§6 invariant: coding never routes into the ACS fan-out (ADR-0202) —
    not even when the prompt happens to contain parallel vocabulary."""
    assert cr._should_delegate(prompt) is False


def test_triage_parallel_words_never_hijack_loop_recurrence() -> None:
    """§6 invariant: LOOP shapes never route into the ACS fan-out. The German
    recurrence form "alle 10 Minuten" must beat the "parallel" wording (D6);
    the task belongs to the scheduler, not a quota-burning one-shot fan-out."""
    assert cr._should_delegate(
        "Prüfe alle 10 Minuten parallel die drei Server auf Erreichbarkeit "
        "und melde Ausfälle") is False


def test_console_directive_block_for_alle_n_minuten_recurrence() -> None:
    """The console-side recurrence supplement must reach Tier 2 as well: the
    direct turn for an "alle N Minuten" task carries the LOOP directive."""
    block = cr._acs_directive_block(
        "Prüfe alle 10 Minuten parallel die drei Server auf Erreichbarkeit")
    assert "<acs_directive" in block
    assert 'primitive="LOOP"' in block


@pytest.mark.parametrize("prompt", [
    # Genuine parallel fan-out wishes must KEEP hitting ACS after the D6
    # reorder — rule 1b still wins where no coding/LOOP shape is present.
    "recherchiere mit 3 workern parallel zu E-Bikes, Lastenrädern und Pedelecs",
    "Vergleiche parallel mit mehreren Workern die Angebote von drei Cloud-Anbietern",
])
def test_triage_genuine_parallel_fanout_still_takes_acs(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True


@pytest.mark.parametrize("prompt", [
    # D6 refutation (2026-07-20): the 0.90 suppression threshold let the whole
    # 0.60–0.85 LOOP band through — an ordinary monitoring verb (überwache /
    # beobachte / watch / monitor = 0.85) plus a bare parallel ADVERB fired
    # rule 1b and burned a compute unit in the fan-out. A bare adverb is far
    # too weak to force ACS; only an EXPLICIT worker/fan-out phrase may.
    "beobachte die Preise mehrerer Anbieter gleichzeitig",
    "überwache die Dashboards parallel",
    "watch multiple dashboards in parallel",
    "monitor the prices in parallel",
    # Recurrence forms the digit-only supplement missed — all must stay DIRECT
    # once a bare adverb no longer forces the fan-out.
    "prüfe stündlich parallel die Verfügbarkeit",
    "mach das zweimal täglich parallel",
    "check every few minutes in parallel",
    "prüfe alle sechs Stunden parallel",
    "mach das jeden Morgen parallel",
])
def test_triage_bare_parallel_adverb_never_forces_fanout(prompt: str) -> None:
    """A bare 'parallel'/'gleichzeitig' adverb must defer to the LOOP/GOAL/
    COMPUTE blueprint (rule 2) and the fan-out-shape gate (rule 3) — it is not,
    on its own, an explicit worker demand and must never burn a quota unit."""
    assert cr._should_delegate(prompt) is False


def test_triage_explicit_worker_overrides_incidental_coding_token() -> None:
    """D6(a) refutation: the reorder wrongly let an incidental coding token
    ('API') suppress an EXPLICIT worker request. A named worker/fan-out demand
    outranks a coding-noun collision (F2/F3/F4 explicit-worker guarantee)."""
    assert cr._should_delegate(
        "sammle unabhängig voneinander aus mehreren Quellen die API-Preise "
        "mit mehreren Workern") is True


def test_triage_german_dative_mehreren_quellen_takes_acs() -> None:
    """D7: the inflected dative "aus mehreren Quellen" matched neither
    `mehrere\\s+quellen` nor `\\bmehrere\\b` — the regexes must cover the
    German flexion forms (mehrere[nrm]?)."""
    assert cr._should_delegate(
        "Recherchiere aus mehreren Quellen die besten E-Bikes und "
        "vergleiche sie") is True


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
    # Regression guard: state.initial.task must carry the real task text,
    # not the historical "web-chat delegated turn (ADR-0114)" placeholder.
    assert spec["state"]["initial"]["task"] == "do the thing"
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


# ── real E2E through acs_runtime.delegation_loop (2026-07-24 root-cause fix) ──
#
# Everything above only ever asserted on the dict `_build_delegation_spec`
# returns — a pure string comparison. It never proved that the real
# `acs_runtime.ACSRuntime.run()` delegation_loop actually threads that dict
# into what a worker subprocess receives. This test drives the REAL manager
# loop (`_manager_loop` / `_dispatch_workers` / `_build_worker_prompt`) with
# only the two LLM subprocess boundaries faked (`_call_manager_sync`,
# `_call_worker_sync`) — the same seam the existing acs_runtime test suite
# already fakes at (see test_call_worker_sync_prompt_delivered_via_stdin) —
# and captures the literal prompt string a worker would be launched with.

def test_delegation_spec_e2e_worker_receives_real_task_not_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    try:
        import acs_runtime as _rt  # type: ignore
        import spawn_gates as _sg  # type: ignore
    except ImportError:
        pytest.skip("acs_runtime/spawn_gates not importable in this environment")
    import asyncio
    import json as _json
    from unittest.mock import patch

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    real_task = "Analysiere die Verkaufszahlen aus drei Quellen und vergleiche sie"
    placeholder = "web-chat delegated turn (ADR-0114)"

    # Exact production call: chat_runtime._build_delegation_spec is what the
    # web-chat delegation route hands to ACSRuntime.run().
    spec = cr._build_delegation_spec(real_task, cr._DELEGATION_BUDGET_DEFAULTS)

    manager_calls = {"n": 0}
    worker_prompts: list[str] = []

    def _fake_manager_sync(prompt, model, tenant_id="_default", proc_holder=None):
        manager_calls["n"] += 1
        if manager_calls["n"] == 1:
            decision = {
                "decision": "DELEGATE",
                "reasoning": "fan out one worker",
                "subtasks": [
                    {"id": "s1", "instructions": "do the sales comparison",
                     "expected_output": {}},
                ],
            }
        else:
            decision = {"decision": "COMPLETE",
                        "complete_artifacts": {"summary": "done"}}
        return _json.dumps(decision), 100

    def _fake_worker_sync(prompt, system, model, budget, extra_env=None,
                           tenant_id="_default", proc_holder=None):
        worker_prompts.append(prompt)
        return (
            _json.dumps({"status": "success", "result": {"ok": True},
                        "confidence": 0.9}),
            50,
            {"engine_id": "claude_code", "model_id": model, "attested": True,
             "locality": "us_cloud"},
        )

    with (
        patch.object(_rt, "_call_manager_sync", side_effect=_fake_manager_sync),
        patch.object(_rt, "_call_worker_sync", side_effect=_fake_worker_sync),
        patch.object(_rt, "_resolve_worker_engine",
                     return_value=("claude_code", "test-model")),
        patch.object(_sg, "check_l44", return_value=None),
        patch.object(_sg, "check_l34", return_value=None),
        patch.object(_sg, "check_l35", return_value=None),
    ):
        runtime = _rt.ACSRuntime(tenant_id="_test", bridge="web", chat="e2e-test",
                                  enable_gate_chain=False)
        result = asyncio.run(runtime.run(spec))

    assert result.status == "success", (result.status, result.error)
    assert manager_calls["n"] == 2
    assert len(worker_prompts) == 1, "expected exactly one worker to be dispatched"

    worker_prompt = worker_prompts[0]
    # The worker prompt renders ctx.state (= spec["state"]["initial"], i.e.
    # `{"task": real_task}` post-fix) verbatim as "CONTEXT STATE" — this is
    # the exact mechanism the root-cause doc describes, proven end-to-end
    # rather than asserted on the spec dict alone.
    assert real_task in worker_prompt, (
        "worker did not receive the real task text through the real "
        "delegation_loop codepath"
    )
    assert placeholder not in worker_prompt, (
        "worker received the historical ADR-0114 placeholder instead of "
        "the real task text"
    )


# ── ADR-0217 — TDE-first delegation: engine choice within the delegated branch ─

@pytest.mark.parametrize("prompt", [
    "Analysiere 500 GB Serverlogs auf Anomalien und fasse die Muster zusammen",
    "Wir haben 2,5 TB Rohdaten aus dem Data Lake — vergleiche die Quartale",
    "Vergleiche 3 Millionen Zeilen Verkaufsdaten aus mehreren Quellen",
    "Process 40 million records from the database dump and compare regions",
    "Big-Data-Auswertung der Clickstreams aus drei Quellen erstellen",
    "Durchsuche riesige Datenmengen aus dem Data Warehouse nach Duplikaten",
    "Aggregate a massive dataset of user events across all shards",
    "Werte 2500000 Einträge aus dem Export aus und vergleiche sie",
])
def test_big_data_prompts_detected(prompt: str) -> None:
    assert cr._is_big_data_task(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Recherchiere die Marktlage aus drei unabhängigen Quellen und vergleiche sie",
    "Vergleiche die drei Cloud-Anbieter aus mehreren Perspektiven",
    "Fasse die Datenbank-Migration zusammen und liste die Risiken auf",  # DB word, no volume
    "Analysiere die Verkaufszahlen aus drei Quellen und vergleiche sie",
    "Erstelle einen großen Bericht über die Lage",  # "großen" without a data noun
])
def test_non_big_data_delegation_prompts_stay_tde(prompt: str) -> None:
    assert cr._is_big_data_task(prompt) is False


def test_engine_target_matrix() -> None:
    f = cr._delegation_engine_target
    # default: TDE
    assert f("Recherchiere aus drei Quellen und vergleiche",
             force_delegate=False, tde_available=True, quota_ok=True) == "tde"
    # explicit /delegate → ACS always
    assert f("Recherchiere aus drei Quellen",
             force_delegate=True, tde_available=True, quota_ok=True) == "acs"
    # big data → ACS
    assert f("Analysiere 500 GB Logs aus drei Quellen",
             force_delegate=False, tde_available=True, quota_ok=True) == "acs"
    # TDE unavailable → ACS (its branch owns the degrade ladder)
    assert f("Recherchiere aus drei Quellen",
             force_delegate=False, tde_available=False, quota_ok=True) == "acs"
    # pool exhausted (peek) → ACS → ADR-0201 fallback ladder
    assert f("Recherchiere aus drei Quellen",
             force_delegate=False, tde_available=True, quota_ok=False) == "acs"


def test_engine_target_is_pure_no_spawn(monkeypatch) -> None:
    """§6 invariant: the triage/choice path must never spawn a subprocess."""
    import subprocess as _sp

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("delegation engine choice spawned a subprocess")

    monkeypatch.setattr(_sp, "Popen", _boom)
    monkeypatch.setattr(_sp, "run", _boom)
    assert cr._delegation_engine_target(
        "Vergleiche 3 Millionen Zeilen aus mehreren Quellen",
        force_delegate=False, tde_available=True, quota_ok=True,
    ) == "acs"


def test_tde_quota_peek_fail_closed(monkeypatch) -> None:
    """A broken license import must peek False (→ ACS branch re-checks
    authoritatively), never True."""
    import builtins
    real_import = builtins.__import__

    def _no_license(name, *a, **k):
        if name.startswith("license"):
            raise ImportError("license module unavailable (test)")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_license)
    monkeypatch.delitem(sys.modules, "license.compute_quota", raising=False)
    monkeypatch.delitem(sys.modules, "license.validator", raising=False)
    assert cr._tde_quota_peek_ok() is False


# ── ADR-0217 rule 1c — big data is delegation-affirmative ─────────────────────

@pytest.mark.parametrize("prompt", [
    # COMPUTE-shaped big data used to fall DIRECT at rule 2 (E2E finding)
    "Analysiere 500 GB Serverlogs aus dem Data Lake auf Anomalie-Muster und vergleiche die Regionen",
    "Process 40 million records from the database dump and compare error rates",
])
def test_big_data_delegates_even_when_compute_shaped(prompt: str) -> None:
    assert cr._should_delegate(prompt) is True
    assert cr._delegation_engine_target(
        prompt, force_delegate=False, tde_available=True, quota_ok=True,
    ) == "acs"


def test_big_data_recurring_stays_scheduler() -> None:
    # Recurrence carve-out: a daily big-data scan is a scheduler task, not ACS.
    assert cr._should_delegate(
        "Überwache täglich die 500 GB Serverlogs auf neue Anomalien"
    ) is False


def test_big_data_named_engine_stays_direct() -> None:
    # Named-engine carve-out: the user chose the direct delegate_* path.
    assert cr._should_delegate(
        "Delegiere an Codex: analysiere die 500 GB Logs aus dem Data Lake"
    ) is False


# ── ADR-0217 round-2: _BIG_DATA_RE precision (review finding 5) ───────────────

@pytest.mark.parametrize("prompt", [
    "Analysiere 1.000.000 Zeilen aus dem Log und vergleiche die Tage",  # grouped de
    "Process 1,000,000 rows from the export and compare",               # grouped en
    "Analysiere 500k rows aus dem Serverlog",                           # k-suffix
    "Serverlogs von 500 GB analysieren",                               # noun-before-volume
    "Werte 2500000 Einträge aus dem Export aus",                       # bare big int
])
def test_big_data_grouped_and_suffix_counts(prompt: str) -> None:
    assert cr._is_big_data_task(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Mein Laptop hat 3 gb RAM, warum ist Chrome so langsam?",   # hardware, not data
    "Mein Server hat 128 GB Arbeitsspeicher, reicht das?",       # high RAM, not data
    "Die GPU hat 24 GB VRAM",
    "Wie installiere ich eine 2 TB SSD?",                        # storage hardware
])
def test_big_data_hardware_volumes_are_not_big_data(prompt: str) -> None:
    # A volume tied to hardware (RAM/VRAM/SSD) must NOT route to the ACS
    # fan-out and burn a compute unit on a smalltalk question.
    assert cr._is_big_data_task(prompt) is False


def test_big_data_regex_no_redos() -> None:
    import time
    t = time.time()
    cr._is_big_data_task("x" * 100_000 + " 500 GB " * 200)
    assert time.time() - t < 1.0


# ── ADR-0217 round-2 refutation: _BIG_DATA_RE rebuilt (ReDoS + precision) ─────

def test_big_data_no_redos_on_digit_blobs() -> None:
    """The refutation found catastrophic O(n²) backtracking (48s freeze) on a
    pasted digit blob. Rebuilt detector must stay linear."""
    import time
    for blob in ("9" * 20000 + " zzz", ",999" * 1200, "1234567 Zeilen " * 2000):
        t = time.time()
        cr._is_big_data_task(blob)
        assert time.time() - t < 0.5, f"slow on {blob[:20]!r}"


@pytest.mark.parametrize("prompt", [
    "Analysiere die 500 GB aus unserem S3-Bucket",   # bucket noun
    "3 Millionen Kundentransaktionen auswerten",       # German compound
    "5 Mio. Messwerte vergleichen",                    # abbrev period + compound
    "12 Terabyte an alten Backups durchsuchen",        # TB + backups
])
def test_big_data_refutation_false_negatives_now_caught(prompt: str) -> None:
    assert cr._is_big_data_task(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Ich habe 3 GB RAM, welche Dateien brauche ich?",   # comma splits clause
    "Fahre 10 km und zähl die Dateien im Ordner",       # "km" unit, not count
    "Ich bin in 3m fertig, schau dir die Dateien an",   # "3m" + comma split
    "Mein Gehalt ist 100000 Euro, erstelle eine Tabelle",  # 6-digit + comma
])
def test_big_data_refutation_false_positives_now_rejected(prompt: str) -> None:
    assert cr._is_big_data_task(prompt) is False


def test_delegate_word_boundary_lockstep() -> None:
    # "/delegatex …" must NOT delegate (parser lockstep with stream_turn).
    assert cr._should_delegate("/delegatex mach was") is False
    assert cr._should_delegate("/delegate mach was") is True
    assert cr._should_delegate("/delegate") is True


# ── ADR-0217 round-3 refutation: O(n²) bound + daten false-friends ────────────

def test_big_data_bounded_on_numeric_blob() -> None:
    """The whole routine (not just each regex) must stay bounded on a
    delimiter-free numeric blob — the round-3 O(n²) finding."""
    import time
    t = time.time()
    cr._is_big_data_task("1234567 " * 20000)  # 160 KB, one clause
    assert time.time() - t < 0.2


@pytest.mark.parametrize("prompt,expected", [
    ("Screene 5 Millionen Kandidaten fuer die Stelle", False),  # -daten false friend
    ("Verarbeite 5 Millionen Verkaufsdaten", True),             # real data compound
    ("3 Millionen Soldaten in der Statistik", False),
])
def test_big_data_daten_false_friends(prompt: str, expected: bool) -> None:
    assert cr._is_big_data_task(prompt) is expected
