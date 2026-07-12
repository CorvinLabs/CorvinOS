"""Regression: the console-chat voice-annotation pipeline (LERN-ZUGABE +
METAPHER) must read the voice profile through the SAME canonical, XDG-aware
loader the console profile editor writes through — not a hardcoded
tenant_home/voice/profile.json. When XDG_CONFIG_HOME is set (interactive /
systemd-user launch), the writer lands in ~/.config/corvin-voice/profile.json
while the old hardcoded reader looked at <corvin_home>/tenants/<tid>/voice/ —
reader != writer, so Learning=3 and Metaphern=on silently did nothing.
"""
import asyncio
import types

import pytest

from corvin_console import chat_runtime as cr


def _stub_summarizer(monkeypatch, kwargs_sink=None):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if kwargs_sink is not None:
            kwargs_sink.append(kw)
        mode = ("APPENDIX" if "--appendix-mode" in argv
                else "METAPHER" if "--metapher-mode" in argv else "?")
        inp = kw.get("input", "")
        return types.SimpleNamespace(stdout=f"{inp} [{mode}]", stderr="", returncode=0)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    return calls


def _fake_profile(monkeypatch, *, chat_render=True, **values):
    # chat_render defaults to True here so the learning/metaphor tests exercise
    # the annotation path; the dedicated chat_render test flips it off.
    class _P:
        @staticmethod
        def load(force=False):
            return dict(values)

        @staticmethod
        def chat_render_enabled():
            return chat_render
    monkeypatch.setattr(cr, "_voice_profile", _P)


def test_learning_and_metaphors_on_produce_suffix(monkeypatch):
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    invoked = [a[-1] for a in calls]
    assert "--appendix-mode" in invoked and "--metapher-mode" in invoked
    assert suffix, "suffix must be non-empty when learning>0 / metaphors=on"


def test_both_off_returns_empty_and_runs_no_subprocess(monkeypatch):
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=0, voice_audience_metaphors="off")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("anything", "_default"))
    assert suffix == ""
    assert calls == [], "no annotation subprocess when both features are off"


def test_reads_canonical_loader_not_tenant_home(monkeypatch):
    """Guard against re-introducing the reader!=writer split: the value comes
    from _voice_profile.load(), independent of any tenant_home file."""
    _stub_summarizer(monkeypatch)
    seen = {}

    class _P:
        @staticmethod
        def load(force=False):
            seen["force"] = force
            return {"voice_audience_learning": 3}

        @staticmethod
        def chat_render_enabled():
            return True
    monkeypatch.setattr(cr, "_voice_profile", _P)
    suffix = asyncio.run(cr._compute_web_annotation_suffix("x.", "any-tenant"))
    assert seen.get("force") is True, "must force-refresh so just-saved toggles apply"
    assert suffix


def test_chat_render_off_suppresses_suffix_even_with_features_on(monkeypatch):
    """Voice-only: with chat_render=off the annex must NOT enter the chat text,
    even when learning/metaphors are on (it still rides the TTS/voice path)."""
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, chat_render=False,
                  voice_audience_learning=3, voice_audience_metaphors="on")
    suffix = asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    assert suffix == "", "chat_render=off must keep the annex out of the chat text"
    assert calls == [], "no annotation subprocess when chat_render is off"


def test_annotation_subprocess_is_latency_bounded(monkeypatch):
    """Every annotation spawn must carry the tight per-call timeout, NOT the old
    60s. On a cold engine that 60s (× 2, plus Hermes fallback) sat on the turn's
    critical path before `done`, freezing the chat composer for 1-2 minutes after
    EVERY turn (verified via live browser E2E). The hard cap keeps a slow machine
    responsive by degrading to no-annotation-this-turn."""
    seen_kwargs = []
    _stub_summarizer(monkeypatch, kwargs_sink=seen_kwargs)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")
    asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    assert seen_kwargs, "expected at least one annotation subprocess"
    for kw in seen_kwargs:
        # The metaphor pass's own timeout is shrunk to whatever remains of a
        # single _ANN_CALL_TIMEOUT_S-sized window since turn start (so a slow
        # appendix call can't add a full fresh 8s on top) — so it may be
        # slightly UNDER the constant, but must never exceed it.
        assert 0 < kw.get("timeout") <= cr._ANN_CALL_TIMEOUT_S, (
            f"annotation subprocess must be hard-capped at "
            f"_ANN_CALL_TIMEOUT_S={cr._ANN_CALL_TIMEOUT_S}, got {kw.get('timeout')}"
        )
    assert cr._ANN_CALL_TIMEOUT_S <= 12, "per-call cap must stay tight (composer freeze ceiling)"


def test_metaphor_skipped_once_budget_spent(monkeypatch):
    """A slow appendix call must NOT chain into a second slow spawn and double
    the composer freeze: once the turn has spent _ANN_TOTAL_BUDGET_S on
    annotation, the (secondary) metaphor pass is skipped."""
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")

    # Simulate a slow appendix: monotonic jumps past the budget between calls.
    ticks = iter([0.0, cr._ANN_TOTAL_BUDGET_S + 1.0, cr._ANN_TOTAL_BUDGET_S + 2.0,
                  cr._ANN_TOTAL_BUDGET_S + 3.0])
    fake_time = types.SimpleNamespace(monotonic=lambda: next(ticks))
    monkeypatch.setattr(cr, "time", fake_time)

    asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    invoked = [a[-1] for a in calls]
    assert "--appendix-mode" in invoked, "appendix (primary) must still run"
    assert "--metapher-mode" not in invoked, "metaphor must be skipped once budget is spent"


def test_metaphor_runs_when_within_budget(monkeypatch):
    """The healthy path is unchanged: a fast appendix leaves budget, so the
    metaphor pass still runs and both suffixes are produced."""
    calls = _stub_summarizer(monkeypatch)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")
    # monotonic barely advances — well within budget.
    ticks = iter([0.0, 0.1, 0.2, 0.3])
    fake_time = types.SimpleNamespace(monotonic=lambda: next(ticks))
    monkeypatch.setattr(cr, "time", fake_time)
    asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    invoked = [a[-1] for a in calls]
    assert "--appendix-mode" in invoked and "--metapher-mode" in invoked


def test_metaphor_timeout_shrinks_to_remaining_window(monkeypatch):
    """Regression: the metaphor pass used to always get a fresh, full
    _ANN_CALL_TIMEOUT_S even when the appendix call had already spent most of
    the annotation budget — so a 4.9s appendix call (just under the 5s
    _ANN_TOTAL_BUDGET_S gate) plus a full 8s metaphor timeout could still
    freeze the composer for ~13s, well past what "total budget" implies. The
    metaphor call's own timeout must shrink to what's left of a single
    _ANN_CALL_TIMEOUT_S-sized window since turn start."""
    seen_kwargs = []
    _stub_summarizer(monkeypatch, kwargs_sink=seen_kwargs)
    _fake_profile(monkeypatch, voice_audience_learning=3, voice_audience_metaphors="on")

    # t0=0.0; appendix call took 4.9s (just under the 5s budget gate, so the
    # metaphor pass still runs); the remaining-budget calc reads the same
    # elapsed time again.
    ticks = iter([0.0, 4.9, 4.9])
    fake_time = types.SimpleNamespace(monotonic=lambda: next(ticks))
    monkeypatch.setattr(cr, "time", fake_time)

    asyncio.run(cr._compute_web_annotation_suffix("The cache stores results.", "_default"))
    # The metaphor call always runs second when both appendix and metaphor
    # fire, so seen_kwargs[1] is the metaphor call's kwargs.
    assert len(seen_kwargs) == 2, "expected both appendix and metaphor calls"
    metaphor_timeout = seen_kwargs[1]["timeout"]
    assert metaphor_timeout <= cr._ANN_CALL_TIMEOUT_S - 4.9 + 0.01, (
        f"metaphor timeout must shrink to the remaining window (~3.1s), "
        f"got {metaphor_timeout}"
    )
    assert metaphor_timeout >= 1.0, "must never shrink below the 1.0s floor"


def test_missing_profile_module_is_safe(monkeypatch):
    monkeypatch.setattr(cr, "_voice_profile", None)
    suffix = asyncio.run(cr._compute_web_annotation_suffix("x.", "_default"))
    assert suffix == ""
