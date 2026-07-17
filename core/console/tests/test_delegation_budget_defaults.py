"""Delegation-budget defaults + the bounded-stop message (2026-07-16).

Maintainer decision: a fresh install must not meet a delegation budget on
ordinary work, because meeting one READS as a failure to someone who has never
seen these numbers. Two halves:

  * defaults raised to generous-but-bounded — deliberately NOT to the ceilings;
  * reaching a budget now reports itself as a bounded stop naming the limit,
    instead of "Delegation fehlgeschlagen: ... status 'budget_exhausted'".

The ceilings themselves are load-bearing and stay put: they exist because one
metered compute unit must not authorize a runaway fan-out. A 100x inflation
(400 workers / 100h) was shipped once and reverted as a quota-defeat, and
acs_validator R32/R35/R36 were added so a silent re-inflation fails loudly.
Defaulting TO the ceilings would hand every free-tier user 64 workers for 24h
out of their single daily compute unit — the same hole, through the front door.
"""
from __future__ import annotations

from corvin_console import chat_runtime as cr
from corvin_console.routes import settings as settings_routes


_SPEC = settings_routes._BUDGET_KEYS
_DEFAULTS = cr._DELEGATION_BUDGET_DEFAULTS


# ── the two sources of truth must agree ──────────────────────────────────────

def test_every_ui_key_has_a_runtime_default() -> None:
    assert set(_SPEC) <= set(_DEFAULTS), (
        "the UI exposes a budget key the runtime has no default for"
    )


def test_the_two_default_tables_agree() -> None:
    """settings.py's `default` is what a fresh install writes; chat_runtime's is
    what an unconfigured run uses. Drift means the console shows one number and
    the run honours another."""
    for key, spec in _SPEC.items():
        assert spec["default"] == _DEFAULTS[key], (
            f"{key}: settings.py default={spec['default']} but "
            f"chat_runtime default={_DEFAULTS[key]}"
        )


# ── generous, but never AT the ceiling ───────────────────────────────────────

def test_no_default_sits_at_its_ceiling() -> None:
    """The whole point of the decision: raise the floor, keep the roof.

    A default equal to max means the first install is already at the ceiling the
    quota-defeat guard exists to enforce, and there is no headroom left for a
    user who genuinely needs more.
    """
    at_ceiling = [k for k, s in _SPEC.items() if s["default"] >= s["max"]]
    assert not at_ceiling, (
        f"{at_ceiling} default to their ceiling — one metered compute unit would "
        "authorize the maximum fan-out on a fresh install"
    )


def test_every_default_is_inside_its_own_validation_range() -> None:
    for key, spec in _SPEC.items():
        assert spec["min"] <= spec["default"] <= spec["max"], (
            f"{key} default {spec['default']} is outside [{spec['min']}, {spec['max']}] "
            "— a fresh install would fail its own save validation"
        )


def test_the_time_and_iteration_knobs_were_raised() -> None:
    """Guards against a silent revert of the maintainer decision.

    Only the knobs that actually stop ordinary work early: a task that needs
    more planning rounds, more worker turns, or more wall-clock is the common
    case. Parallelism is not (see the next test).
    """
    assert _DEFAULTS["max_loops"] >= 20
    assert _DEFAULTS["max_wall_time"] >= 4 * 3600
    assert _DEFAULTS["timeout_seconds"] >= 4 * 3600
    assert _DEFAULTS["max_worker_turns"] >= 300


def test_the_fanout_knobs_were_deliberately_left_alone() -> None:
    """The quota-defeat is measured in worker-hours per metered compute unit.

    max_total_workers is that figure's numerator and max_depth its exponent, so
    neither rides along with a UX-motivated raise: the decision buys longer and
    deeper single-track runs, not a wider fan-out.
    """
    assert _DEFAULTS["max_total_workers"] == 8      # pre-inflation value, unchanged
    assert _DEFAULTS["max_depth"] == 4
    worker_hours = _DEFAULTS["max_total_workers"] * (_DEFAULTS["max_wall_time"] / 3600)
    assert worker_hours <= 32, (
        f"{worker_hours} worker-hours per metered unit (old default 8, UI ceiling "
        "1536, the 2026 defeat 40000) — this is the axis that must not drift"
    )


def test_max_depth_was_deliberately_not_raised() -> None:
    """Depth is the fan-out EXPONENT, not a linear knob: raising it multiplies
    the worker ceiling rather than adding to it. acs_validator R32 caps it at 10.
    """
    assert _DEFAULTS["max_depth"] == 4


def test_headroom_is_left_for_users_who_genuinely_need_more() -> None:
    """"Generous by default" must not mean "nothing left to give".

    Someone with a real long-running task should still be able to raise the knob
    in Settings — which is exactly what the new bounded-stop message tells them
    to do, so it has to be true.
    """
    assert _DEFAULTS["max_wall_time"] * 2 <= _SPEC["max_wall_time"]["max"]
    assert _DEFAULTS["max_loops"] * 2 <= _SPEC["max_loops"]["max"]
    assert _DEFAULTS["max_total_workers"] * 2 <= _SPEC["max_total_workers"]["max"]


# ── a reached budget is a bounded stop, not a crash ──────────────────────────

def test_the_stop_message_names_the_limit_that_was_hit() -> None:
    msg = cr._budget_stop_message("max_loops=20 reached", 20, 3, german=True)
    assert "fehlgeschlagen" not in msg.lower(), "a budget stop must not read as a failure"
    assert "Planungsrunden" in msg          # the limit, in plain language
    assert "max_loops" in msg               # the exact key to change
    assert "Settings" in msg                # where to change it
    assert "20 Runde(n)" in msg             # what was achieved before stopping


def test_the_stop_message_speaks_english_for_english_users() -> None:
    """The final result text is SPOKEN by the voice pipeline — a hard-German
    message switched the voice language mid-session for English users."""
    msg = cr._budget_stop_message("max_loops=20 reached", 20, 3, german=False)
    assert "failed" not in msg.lower()
    assert "planning rounds" in msg
    assert "max_loops" in msg
    assert "Settings" in msg
    assert "20 round(s)" in msg
    assert "Budget erreicht" not in msg


def test_each_known_breach_gets_a_plain_language_name() -> None:
    for key in ("max_loops", "max_total_workers", "max_wall_time",
                "max_total_tokens", "max_tool_calls"):
        for german, lang in ((True, "de"), (False, "en")):
            msg = cr._budget_stop_message(f"{key}=7 reached", 1, 1, german=german)
            assert cr._BUDGET_LABELS[key][lang] in msg


def test_an_unknown_breach_still_produces_a_sane_message() -> None:
    """A new BudgetEnvelope knob must not produce a broken sentence."""
    msg = cr._budget_stop_message("max_something_new=3 reached", 2, 0, german=True)
    assert "Budget erreicht" in msg
    assert "None" not in msg
    msg_en = cr._budget_stop_message("max_something_new=3 reached", 2, 0, german=False)
    assert "Budget reached" in msg_en
    assert "None" not in msg_en


def test_an_empty_breach_does_not_crash() -> None:
    msg = cr._budget_stop_message("", None, None, german=True)
    assert "Budget erreicht" in msg
    assert "Bis dahin" not in msg   # nothing to report, so nothing claimed


def test_message_language_follows_the_users_own_prompt() -> None:
    """German prompt → German status text; everything else → English (repo
    rule: user-facing runtime text defaults to English). The bilingual-trap
    words ("was", "in", "an") must not flip a short German prompt to English —
    the same defect class as the voice text-first language detection."""
    assert cr._prompt_is_german("Bitte erstelle eine Übersicht über alle Dateien.")
    assert cr._prompt_is_german("Was war in Datei A los? Bitte prüfen und berichten.")
    assert cr._prompt_is_german("Baue das Login-Formular neu")
    assert cr._prompt_is_german("Analysiere das Repo komplett")
    assert cr._prompt_is_german("Schreib mir ein Skript, das Logs parst")
    assert not cr._prompt_is_german("Please create an overview of all files in the repo.")
    assert not cr._prompt_is_german("Fix the failing test and report what was wrong.")
    # umlauts are strong evidence, but capped at +2 TOTAL: English prompts
    # containing German proper nouns stay English even with several of them
    # (the refutation counter-examples, both rounds)
    assert not cr._prompt_is_german("Send an email to Jürgen about the Q3 report")
    assert not cr._prompt_is_german("Rename the file to München-data.csv and commit it")
    assert not cr._prompt_is_german(
        "Can you email Jürgen Müller about the Zürich meeting?")
    # "will"/"file(s)" are de/en-ambiguous (German modal verb, Denglisch) and
    # must not flip a short German prompt
    assert cr._prompt_is_german("Will alle Files checken")
    assert not cr._prompt_is_german("")   # tie/empty defaults to English


# ── the defaults must survive the ACS clamp chain ────────────────────────────

def test_the_raised_defaults_are_what_acs_actually_enforces() -> None:
    """A default the runtime clamps back down is a lie in the Settings UI.

    acs_runtime._clamp_positive_cap(value, default, ceiling) is min(value,
    ceiling) for positive values — the `default` arm only fires for <= 0 — so
    raising past an old hardcoded `default` argument is safe. That is worth
    pinning rather than re-deriving: reader-disagrees-with-writer is how this
    subsystem breaks, and the Settings page now promises these numbers to users.
    """
    import importlib.util
    import sys
    from pathlib import Path as _P

    # Load by FILE PATH under a private name rather than `import acs_runtime`:
    # conftest.py snapshots/restores sys.modules between tests, so a plain
    # import resolves to whatever a previously-run test left behind (this test
    # passed alone and failed in the suite — pollution, not a real defect).
    shared = _P(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    spec_mod = importlib.util.spec_from_file_location(
        "_acs_runtime_for_budget_test", shared / "acs_runtime.py")
    if spec_mod is None or spec_mod.loader is None:
        import pytest
        pytest.skip("acs_runtime.py not found in this environment")
    sys.path.insert(0, str(shared))   # acs_runtime imports its own siblings
    acs = importlib.util.module_from_spec(spec_mod)
    # Register BEFORE exec: acs_runtime's dataclasses resolve their annotations
    # through sys.modules, and without this exec_module dies with
    # "AttributeError: 'NoneType' object has no attribute '__dict__'" — which the
    # except-below would have swallowed into a permanent skip, i.e. a test that
    # proves nothing while looking fine.
    sys.modules[spec_mod.name] = acs
    try:
        spec_mod.loader.exec_module(acs)
    finally:
        sys.modules.pop(spec_mod.name, None)   # don't leak into other tests
        # conftest.py restores sys.modules but NOT sys.path — leaving the
        # shared dir at the front of the path is exactly the cross-test
        # pollution this test's own loader comment complains about.
        try:
            sys.path.remove(str(shared))
        except ValueError:
            pass

    spec = cr._build_delegation_spec("a two-step task", _DEFAULTS)
    env = acs._budget_from_spec(spec)
    for key in ("max_loops", "max_wall_time", "max_total_workers"):
        assert getattr(env, key) == _DEFAULTS[key], (
            f"{key}: Settings shows {_DEFAULTS[key]} but ACS enforces "
            f"{getattr(env, key)}"
        )
    # Per-worker-call knobs, pinned along the REAL production path
    # (spec → _budget_from_spec → envelope → _worker_budget_for_spawn →
    # _call_worker_sync). Two prior generations of this pin were lies:
    # first the spawn hard-clamped timeout_seconds to 1800/3600 (raised
    # Settings were a no-op), then a fix read it from the manager-LLM's
    # budget_allocation dict — which never carries it — so the knob stayed
    # dead while a unit test fed the function the Settings dict it never
    # receives in production (refutation finding 2026-07-17).
    assert env.timeout_seconds == _DEFAULTS["timeout_seconds"]
    assert env.max_worker_turns == _DEFAULTS["max_worker_turns"]
    spawn = acs._worker_budget_for_spawn(env, {})
    assert spawn["timeout_seconds"] == _DEFAULTS["timeout_seconds"], (
        f"Settings shows {_DEFAULTS['timeout_seconds']} but a worker spawn "
        f"enforces {spawn['timeout_seconds']}"
    )
    assert spawn["max_worker_turns"] == _DEFAULTS["max_worker_turns"]
    assert acs._effective_worker_timeout(spawn) == _DEFAULTS["timeout_seconds"]


def test_the_manager_llm_cannot_raise_the_operator_bounds() -> None:
    """budget_allocation is manager-LLM output — the same trust level as the
    subtask id the runtime sanitizes for traversal. It may LOWER the
    operator's per-call bounds (a trivial subtask deserves a short leash) but
    must never raise them: a prompt-injected `timeout_seconds: 86400` must
    not buy a hung worker 24 h of slot + spend."""
    import importlib.util
    import sys
    from pathlib import Path as _P
    shared = _P(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    spec_mod = importlib.util.spec_from_file_location(
        "_acs_runtime_for_spawn_test", shared / "acs_runtime.py")
    if spec_mod is None or spec_mod.loader is None:
        import pytest
        pytest.skip("acs_runtime.py not found in this environment")
    sys.path.insert(0, str(shared))
    acs = importlib.util.module_from_spec(spec_mod)
    sys.modules[spec_mod.name] = acs
    try:
        spec_mod.loader.exec_module(acs)
    finally:
        sys.modules.pop(spec_mod.name, None)
        try:
            sys.path.remove(str(shared))
        except ValueError:
            pass

    env = acs._budget_from_spec(
        cr._build_delegation_spec("a two-step task", _DEFAULTS))
    # raise attempts are clamped back to the operator's values
    raised = acs._worker_budget_for_spawn(
        env, {"timeout_seconds": 86400, "max_worker_turns": 99999})
    assert raised["timeout_seconds"] == _DEFAULTS["timeout_seconds"]
    assert raised["max_worker_turns"] == _DEFAULTS["max_worker_turns"]
    # lower attempts are honoured
    lowered = acs._worker_budget_for_spawn(
        env, {"timeout_seconds": 120, "max_worker_turns": 5})
    assert lowered["timeout_seconds"] == 120
    assert lowered["max_worker_turns"] == 5
    # garbage is ignored, not crashed on
    garbage = acs._worker_budget_for_spawn(
        env, {"timeout_seconds": "yes please", "max_worker_turns": None})
    assert garbage["timeout_seconds"] == _DEFAULTS["timeout_seconds"]
    assert garbage["max_worker_turns"] == _DEFAULTS["max_worker_turns"]
    # the spawn is deadlined against REMAINING wall time: a nearly-exhausted
    # envelope caps the timeout at the 60 s floor instead of its full value
    env.start_time -= env.max_wall_time  # pretend the run used its wall time
    exhausted = acs._worker_budget_for_spawn(env, {})
    assert exhausted["timeout_seconds"] == 60
