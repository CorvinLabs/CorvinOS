"""E2E + wiring proof: OS turns route across THREE tiers — ADR-0952.

Operator report, 2026-09-20: *"wieso wird hier immer haiku verwendet — sollte
nicht medium sonnet 5 und complex opus 5 sein"*, against a Models console whose
"Workload by complexity" panel showed `haiku-4-5` on all three tiers
(67 / 65 / 69 turns).

Four independent causes, each sufficient on its own, all measured on the live
install before the fix:

1. **A hard pin.** `spec.engine_models.claude_code.os_model:
   claude-haiku-4-5-20251001` is Tier 2.5 and returns immediately, so Tiers
   2.7 / 2.8 / 2.9 / 3 were unreachable. `resolve_os_model(..., payload_chars=
   50_000)` returned Haiku.
2. **Tier 2.9 did not exist.** Written 2026-09-15 in `4bf77ef9` into
   `operator/bridges/shared/model_selector.py`; commit `459047ed` the same day
   created `corvin_operator/bridges/shared/model_selector.py` from a
   pre-Tier-2.9 copy during the ADR-0730 rename, and `operator/` was deleted.
   The CALLER survived: `chat_runtime.py` still passed `task_input=prompt`, so
   every console turn raised `TypeError: resolve_os_model() got an unexpected
   keyword argument 'task_input'` into a surrounding `except Exception:
   _os_model = None`. Chain evidence: `os_turn.started` with `model: ""` on all
   5 `channel: web` turns, a real id on the 295 bridge turns.
3. **`claude-opus-5` was not in `_MODEL_RANK`**, so it ranked 0 — below Haiku.
   The cache guard would have read the classifier's Opus answer as a
   *downgrade* and refused it, and `_FLOOR_TO_MODEL["opus"]` pointed at
   `claude-opus-4-7`, a model this registry does not carry.
4. **The operator's own saved mapping could never apply.** Settings → AI
   Engines already held simple/medium/complex = haiku-4-5 / sonnet-5 / opus-5,
   persisted provider-qualified (`anthropic/claude-opus-5`), while the engine
   registry stores bare ids and `model_is_registered` is exact-match
   fail-closed. All three tiers would have abstained with `not_registered`.

Transport note: these drive the REAL `model_selector.resolve_os_model` — the
one function both surfaces call — against a real tenant YAML and the real
engine registry. The turn-level proof that the chosen id reaches the CLI argv
lives in `test_os_model_tier29_classifier_e2e.py`, which owns the WebSocket
fixture; duplicating it here would add a second flaky dependency without a
second assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import engine_models as EM  # noqa: E402
import model_selector as MS  # noqa: E402

# Tier 2.9 never imports the classifier on the hot path — it abstains until
# `model_selector._warm_classifier`'s daemon thread has put the module in
# `sys.modules` (a 1.09 s numpy import that would otherwise stall the console's
# event loop). Importing it HERE removes the race from these assertions, and is
# its own positive control: an import failure fails loudly instead of leaving
# every test below silently measuring an abstain.
import core.skills.os_skills.model_selector  # noqa: E402,F401


# Prompts chosen to land in each branch of `_classify_complexity`'s rule set
# (SIMPLE < 500 tokens; COMPLEX > 3000 tokens or >5 code blocks or >10 deps;
# MEDIUM is the residual band). Token estimate drives them, so the length is
# the payload, not decoration.
_SENTENCE = "Analyse the delegation path and describe how the worker span is written. "
SIMPLE_PROMPT = "Was ist 2+2?"
MEDIUM_PROMPT = _SENTENCE * 120
COMPLEX_PROMPT = _SENTENCE * 600

#: The branch that must STAY out: `complex` at 0.70 from a keyword hint with no
#: measured signal. It is what keeps the confidence table from being a guard
#: that can never fire.
KEYWORD_ONLY_COMPLEX = "Design a complex distributed architecture"


def _tenant_home(tmp_path: Path, spec: str):
    """A tenant home holding *spec*, bound for the duration of one test.

    ``CORVIN_HOME`` is saved and restored HERE rather than through
    ``monkeypatch.setenv``, and the final ``load_providers(force_reload=True)``
    runs only AFTER the restore. Order matters and is not cosmetic: monkeypatch
    undoes env vars in its own finalizer, which runs *after* a fixture's
    teardown, so a reload in the teardown re-caches the provider registry
    against a ``tmp_path`` that pytest is about to delete. Every later test in
    the SESSION then reads that dead path — measured 2026-09-20: the tier29
    WebSocket suite went from 8/8 green alone to 5 fixture timeouts at ~39 s
    whenever this file ran before it, which reads exactly like flakiness in the
    other suite.
    """
    import contextlib
    import os

    @contextlib.contextmanager
    def _bind():
        home = tmp_path / ".corvin"
        cfg = home / "tenants" / "_default" / "global"
        cfg.mkdir(parents=True)
        (cfg / "tenant.corvin.yaml").write_text(spec)
        previous = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        EM.load_providers(force_reload=True)
        try:
            yield home
        finally:
            if previous is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = previous
            EM.load_providers(force_reload=True)

    return _bind()


@pytest.fixture
def unpinned_tenant(tmp_path):
    """A tenant with NO `os_model` pin, so the ladder below Tier 2.5 is live."""
    with _tenant_home(tmp_path,
                      "spec:\n  engine_models:\n    claude_code:\n"
                      "      worker_model: claude-opus-5\n") as home:
        yield home


@pytest.fixture
def pinned_tenant(tmp_path):
    """The live shape before this change: a hard `os_model` pin."""
    with _tenant_home(tmp_path,
                      "spec:\n  engine_models:\n    claude_code:\n"
                      "      os_model: claude-haiku-4-5-20251001\n"
                      "      worker_model: claude-opus-5\n") as home:
        yield home


def _resolve(prompt: str, **kw):
    return MS.resolve_os_model(
        None, payload_chars=kw.pop("payload_chars", len(prompt)),
        tenant_id="_default", task_input=prompt, **kw,
    )


# ── 0. reachability ──────────────────────────────────────────────────


class TestTheTierIsWiredOnBothSurfaces:
    """Cause 2 was a caller that outlived its callee. Both halves are asserted
    here, because either one alone is silently inert."""

    def test_the_resolver_has_the_tier(self):
        assert hasattr(MS, "classify_os_model"), "Tier 2.9 is gone again"
        src = Path(MS.__file__).read_text(encoding="utf-8")
        body = src.split("def resolve_os_model(", 1)[1]
        assert "classify_os_model(" in body, (
            "classify_os_model exists but resolve_os_model does not call it — "
            "the tier is dead code"
        )

    def test_the_resolver_accepts_the_kwarg_its_callers_pass(self):
        import inspect

        assert "task_input" in inspect.signature(MS.resolve_os_model).parameters, (
            "chat_runtime and adapter both pass task_input=; without the "
            "parameter every call raises TypeError into a swallowing except"
        )

    @pytest.mark.parametrize("surface, rel", [
        ("console", "core/console/corvin_console/chat_runtime.py"),
        ("bridge", "corvin_operator/bridges/shared/adapter.py"),
    ])
    def test_both_surfaces_hand_over_the_task_text(self, surface, rel):
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert "task_input=prompt" in src, (
            f"the {surface} surface stopped passing the task text — its turns "
            f"silently fall back to the payload-size-only tier"
        )


# ── 1. the ladder ────────────────────────────────────────────────────


class TestThreeTierLadder:
    """The operator's question, as three assertions."""

    def test_simple_routes_to_haiku(self, unpinned_tenant):
        assert _resolve(SIMPLE_PROMPT) == "claude-haiku-4-5-20251001"

    def test_medium_routes_to_sonnet_5(self, unpinned_tenant):
        assert _resolve(MEDIUM_PROMPT) == "claude-sonnet-5"

    def test_complex_routes_to_opus_5(self, unpinned_tenant):
        assert _resolve(COMPLEX_PROMPT) == "claude-opus-5", (
            "cause 3: an id missing from _MODEL_RANK ranks 0 — below Haiku — "
            "so the cache guard reads the escalation as a downgrade"
        )

    def test_the_three_are_actually_different(self, unpinned_tenant):
        picked = {
            _resolve(SIMPLE_PROMPT),
            _resolve(MEDIUM_PROMPT),
            _resolve(COMPLEX_PROMPT),
        }
        assert len(picked) == 3, (
            f"all tiers collapsed onto {picked} — this is the reported symptom"
        )


# ── 2. the pin still wins ────────────────────────────────────────────


class TestPinPrecedenceIsUnchanged:
    """Tier 2.5 above Tier 2.9 is the contract, not an accident: an operator
    who pins a model must get it, and 'the classifier overrode my pin' would
    be a worse bug than the one being fixed."""

    @pytest.mark.parametrize("prompt", [SIMPLE_PROMPT, MEDIUM_PROMPT, COMPLEX_PROMPT])
    def test_a_pinned_tenant_never_reaches_the_classifier(self, pinned_tenant, prompt):
        assert _resolve(prompt) == "claude-haiku-4-5-20251001"

    def test_an_explicit_profile_model_still_wins(self, unpinned_tenant):
        assert MS.resolve_os_model(
            {"model": "claude-fable-5"}, payload_chars=len(COMPLEX_PROMPT),
            tenant_id="_default", task_input=COMPLEX_PROMPT,
        ) == "claude-fable-5"


# ── 3. the abstain guards ────────────────────────────────────────────


class TestAbstainGuards:
    def test_a_keyword_only_verdict_does_not_route(self, unpinned_tenant):
        """The confidence table's live subject. `complex` at 0.70 comes from a
        keyword with nothing measured behind it; admitting it would make the
        table a guard that can never fire."""
        assert MS.classify_os_model(KEYWORD_ONLY_COMPLEX, tenant_id="_default") is None

    def test_an_abstain_falls_through_rather_than_failing_the_turn(self, unpinned_tenant):
        """Tier 3 returns Sonnet 5 unconditionally, so the fallback is a real
        model — never None, never an exception."""
        assert _resolve(KEYWORD_ONLY_COMPLEX) == "claude-sonnet-5"

    def test_the_tier_abstains_until_the_classifier_is_warm(
        self, unpinned_tenant, monkeypatch
    ):
        """The hot path must never pay the 1.09 s import itself. Before the
        warm-up thread finishes, the tier falls through to Tier 3 (Sonnet 5,
        the pre-existing default) — it does not import, and it does not
        block."""
        import time

        monkeypatch.delitem(
            sys.modules, "core.skills.os_skills.model_selector", raising=False)
        started = time.perf_counter()
        assert MS.classify_os_model(COMPLEX_PROMPT, tenant_id="_default") is None
        assert time.perf_counter() - started < 0.2, (
            "the tier imported the classifier on the hot path — inside "
            "stream_turn's async generator that stalls the whole event loop"
        )

    def test_an_empty_task_input_skips_the_tier_entirely(self, unpinned_tenant):
        assert MS.resolve_os_model(
            None, payload_chars=1000, tenant_id="_default") == "claude-sonnet-5"

    def test_a_downgrade_is_refused_on_a_large_context(self, unpinned_tenant):
        """Switching model invalidates the prompt cache. On a long chat the
        re-write costs more than serving from the established cache, so the
        cheap answer would RAISE spend while looking like a saving."""
        big = MS.threshold_chars() + 1
        assert MS.classify_os_model(
            SIMPLE_PROMPT, tenant_id="_default", payload_chars=big) is None

    def test_escalation_is_never_refused_however_large(self, unpinned_tenant):
        big = MS.threshold_chars() * 20
        assert MS.classify_os_model(
            COMPLEX_PROMPT, tenant_id="_default", payload_chars=big) == "claude-opus-5"

    def test_the_autoselect_kill_switch_also_disables_the_tier(
        self, unpinned_tenant, monkeypatch
    ):
        """``CORVIN_OS_MODEL_AUTOSELECT=off`` means "do not pick a model for
        me". Tier 2.9 IS automatic selection, so leaving it outside the switch
        would make the switch silently stop killing — it would still emit a
        ``--model`` flag the operator asked not to have. An explicit pin is
        unaffected: the switch is about automatic choice, not the operator's
        own."""
        monkeypatch.setenv("CORVIN_OS_MODEL_AUTOSELECT", "off")
        assert _resolve(COMPLEX_PROMPT) is None
        assert MS.resolve_os_model(
            {"model": "claude-fable-5"}, payload_chars=10,
            tenant_id="_default", task_input=COMPLEX_PROMPT,
        ) == "claude-fable-5"

    def test_a_classifier_failure_costs_nobody_their_turn(self, unpinned_tenant, monkeypatch):
        monkeypatch.setattr(
            MS, "classify_os_model",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert _resolve(COMPLEX_PROMPT) == "claude-sonnet-5"


# ── 4. registry admissibility ────────────────────────────────────────


class TestRegistryAdmissibility:
    """Cause 4: the operator's own saved mapping is provider-qualified and the
    registry is not."""

    def test_a_provider_qualified_id_resolves(self):
        assert MS.resolve_registry_id(
            "anthropic/claude-opus-5", "claude_code") == "claude-opus-5"

    def test_a_family_id_resolves_to_its_dated_snapshot(self):
        assert MS.resolve_registry_id(
            "claude-haiku-4-5", "claude_code") == "claude-haiku-4-5-20251001"

    def test_a_foreign_model_is_refused_not_guessed(self):
        for foreign in ("openai/gpt-4-turbo", "mistral:7b", "ollama/qwen3:8b"):
            assert MS.resolve_registry_id(foreign, "claude_code") is None, foreign

    def test_an_unknown_engine_is_refused(self):
        assert MS.resolve_registry_id("claude-opus-5", "no-such-engine") is None

    def test_empty_input_is_refused(self):
        assert MS.resolve_registry_id("", "claude_code") is None
        assert MS.resolve_registry_id("anthropic/", "claude_code") is None


# ── 5. the ordering table ────────────────────────────────────────────


class TestRankingCoversEveryRoutableModel:
    """An id absent from `_MODEL_RANK` ranks 0 — below Haiku — and every
    comparison in the module then treats it as the cheapest thing available.
    That is cause 3, and it is invisible until a tier actually picks the id."""

    @pytest.mark.parametrize("model", ["claude-haiku-4-5-20251001",
                                       "claude-sonnet-5", "claude-opus-5"])
    def test_every_ladder_model_is_ranked(self, model):
        assert MS._MODEL_RANK.get(model, 0) > 0

    def test_the_ladder_is_strictly_ordered(self):
        assert (MS._MODEL_RANK["claude-haiku-4-5-20251001"]
                < MS._MODEL_RANK["claude-sonnet-5"]
                < MS._MODEL_RANK["claude-opus-5"])

    def test_the_opus_floor_names_a_registered_model(self):
        floor = MS._FLOOR_TO_MODEL["opus"]
        assert floor == MS.DEFAULT_TOP
        assert EM.model_is_registered(floor, "claude_code"), (
            "the opus floor pointed at claude-opus-4-7, which this registry "
            "does not carry — apply_floor would have named an unrunnable model"
        )

    def test_apply_floor_can_reach_the_top_tier(self):
        assert MS.apply_floor("claude-haiku-4-5-20251001", "opus") == MS.DEFAULT_TOP

    def test_apply_floor_never_downgrades(self):
        assert MS.apply_floor(MS.DEFAULT_TOP, "haiku") == MS.DEFAULT_TOP


# ── 6. the decision is auditable ─────────────────────────────────────


class TestTheDecisionIsAuditable:
    def test_every_audit_field_is_allowlisted(self):
        """ADR-0129 M2: an event type with no registered allowlist is
        default-DENY, so an unlisted field lands as `_dropped_fields` and the
        routing decision becomes unauditable."""
        assert MS._ALLOWED_FIELDS_CLASSIFIED >= {
            "complexity", "confidence", "selected_model", "engine", "tier",
            "payload_chars", "outcome",
        }

    def test_the_record_reaches_the_chain_for_an_applied_decision(
        self, unpinned_tenant, tmp_path, monkeypatch
    ):
        import json

        chain = tmp_path / "audit.jsonl"
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
        assert MS.classify_os_model(COMPLEX_PROMPT, tenant_id="_default") == "claude-opus-5"

        assert chain.exists(), "no audit record was written at all"
        rows = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
        classified = [r for r in rows if r.get("event_type") == "os_model.classified"]
        assert classified, "the routing decision left no os_model.classified record"
        details = classified[-1].get("details") or {}
        assert details.get("outcome") == "applied"
        assert details.get("selected_model") == "claude-opus-5"
        assert details.get("complexity") == "complex"
        assert not details.get("_dropped_fields"), (
            f"the field floor dropped {details.get('_dropped_fields')}"
        )

    def test_an_abstain_is_recorded_too(self, unpinned_tenant, tmp_path, monkeypatch):
        """A decision not taken is still a decision — otherwise the only
        inspectable outcome is the one that needed no explaining."""
        import json

        chain = tmp_path / "audit.jsonl"
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
        assert MS.classify_os_model(KEYWORD_ONLY_COMPLEX, tenant_id="_default") is None

        rows = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
        classified = [r for r in rows if r.get("event_type") == "os_model.classified"]
        assert classified
        assert (classified[-1]["details"]["outcome"]).startswith("abstain")

    def test_no_task_text_is_in_the_record(self, unpinned_tenant, tmp_path, monkeypatch):
        """L16/L34: metadata only. The prompt must not reach the chain."""
        chain = tmp_path / "audit.jsonl"
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
        MS.classify_os_model(COMPLEX_PROMPT, tenant_id="_default")
        assert "Analyse the delegation path" not in chain.read_text()
