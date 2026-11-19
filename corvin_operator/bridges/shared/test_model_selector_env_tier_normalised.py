"""Tier models taken from the environment reach the CLI in a form it accepts.

``CORVIN_OS_MODEL_HIGH`` / ``CORVIN_OS_MODEL_LOW`` were returned verbatim by
``high_model()`` / ``low_model()`` and from there by Tier 3 (autoselect), Tier 2.8
(ATO), the error escalation and the savings cap. A provider-qualified value
(``anthropic/claude-sonnet-5``) therefore went to ``--model`` unresolved while every
pin tier already passed through ``normalise_pin``.

The assertions go through ``resolve_os_model_with_source`` — the function both
surfaces call — not only through the two helpers.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

SHARED = Path(__file__).resolve().parent
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import model_selector as ms  # type: ignore  # noqa: E402

_VARS = ("CORVIN_OS_MODEL_OVERRIDE", "CORVIN_OS_MODEL_AUTOSELECT", "CORVIN_OS_MODEL_ALLOW_HAIKU",
         "CORVIN_OS_MODEL_HIGH", "CORVIN_OS_MODEL_LOW", "CORVIN_OS_MODEL_THRESHOLD_CHARS")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    return monkeypatch


def test_positive_control_the_pin_normaliser_strips_the_prefix(env):
    # Without this the tests below would pass for a normaliser that does nothing.
    assert ms.normalise_pin("anthropic/claude-sonnet-5", "claude_code") == "claude-sonnet-5"


def test_high_model_from_the_environment_is_not_provider_qualified(env):
    env.setenv("CORVIN_OS_MODEL_HIGH", "anthropic/claude-sonnet-5")
    assert ms.high_model("_default") == "claude-sonnet-5"
    assert ms.escalate_for_error("prompt is too long", current="claude-haiku-4-5") in (
        None, "claude-sonnet-5")


def test_tier_3_returns_the_resolved_high_model(env):
    env.setenv("CORVIN_OS_MODEL_HIGH", "anthropic/claude-sonnet-5")
    model, source = ms.resolve_os_model_with_source({}, payload_chars=10, tenant_id="_default")
    assert source == "autoselect"
    assert model == "claude-sonnet-5"
    assert "/" not in model


def test_tier_3_returns_the_resolved_low_model_when_haiku_is_allowed(env):
    env.setenv("CORVIN_OS_MODEL_ALLOW_HAIKU", "1")
    env.setenv("CORVIN_OS_MODEL_LOW", "anthropic/claude-haiku-4-5")
    model, source = ms.resolve_os_model_with_source({}, payload_chars=10, tenant_id="_default")
    assert source == "autoselect"
    assert "/" not in model
    # The family id is mapped onto whatever the registry holds for it (a dated snapshot
    # where that is the only entry); either way it is the registry's own id.
    assert model == ms.resolve_registry_id("claude-haiku-4-5", "claude_code")


def test_tier_2_8_maps_the_ato_shorthand_onto_resolved_models(env):
    env.setenv("CORVIN_OS_MODEL_HIGH", "anthropic/claude-sonnet-5")
    model, source = ms.resolve_os_model_with_source(
        {}, payload_chars=10, tenant_id="_default",
        ato_plan_hint={"recommended_model": "sonnet", "confidence": 0.9})
    assert (model, source) == ("claude-sonnet-5", "ato")


def test_an_unverifiable_environment_value_is_kept_not_dropped(env):
    # A pin is the operator's instruction: an id the registry does not know passes
    # through (minus a provider prefix) instead of being replaced by another model.
    env.setenv("CORVIN_OS_MODEL_HIGH", "anthropic/claude-sonnet-99-unreleased")
    assert ms.high_model("_default") == "claude-sonnet-99-unreleased"
    env.setenv("CORVIN_OS_MODEL_HIGH", "opusplan")
    assert ms.high_model("_default") == "opusplan"


def test_the_newest_version_of_a_family_is_returned_as_the_registry_id(env, monkeypatch):
    """Tier 2.9: where the registry holds the newest version only as a dated snapshot,
    the classifier tier returns that snapshot, not the bare family id."""
    calls = {}

    def fake_resolve(model, engine_id):
        calls.setdefault("seen", []).append(model)
        return {"claude-haiku-4-5": "claude-haiku-4-5-20251001",
                "claude-haiku-9": "claude-haiku-9-20270101"}.get(model)

    class _Result:
        complexity = "simple"
        confidence = 0.85
        recommended_model = "claude-haiku-4-5"

    class _Selector:
        def __init__(self, overrides=None):
            pass

        def classify(self, task_input, tenant_id):
            return _Result()

    class _Lineage:
        @staticmethod
        def family_of(model_id):
            return "haiku"

        @staticmethod
        def rank(model_id):
            return 1

    fake = types.ModuleType("core.skills.os_skills.model_selector")
    fake.ModelSelector = _Selector
    monkeypatch.setitem(sys.modules, "core.skills.os_skills.model_selector", fake)
    monkeypatch.setattr(ms, "resolve_registry_id", fake_resolve)
    monkeypatch.setattr(ms, "tier_model", lambda fam: "claude-haiku-9")
    monkeypatch.setattr(ms, "_lineage", lambda: _Lineage())
    monkeypatch.setattr(ms, "_tier_saved", lambda tenant_id, complexity: False)
    monkeypatch.setattr(ms, "high_model", lambda tenant_id=None: "claude-sonnet-5")
    out = ms.classify_os_model("hi", tenant_id="_default", engine_id="claude_code",
                               payload_chars=5)
    assert "claude-haiku-9" in calls["seen"]          # the newest-version step was reached
    assert out == "claude-haiku-9-20270101"
