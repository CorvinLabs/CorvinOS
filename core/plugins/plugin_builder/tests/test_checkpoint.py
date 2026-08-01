"""checkpoint.py — the ADR-0262 Zwischenstand summary (verbatim risk flags,
optional voice polishing)."""
from __future__ import annotations

from plugin_builder.checkpoint import build_checkpoint
from plugin_builder.classifier import classify
from plugin_builder.models import Constraints, DependencySpec, PluginIdea, ProblemStatement


def _idea(**overrides):
    defaults = dict(
        plugin_name="Test Plugin",
        problem=ProblemStatement("solves a problem", "someone", "", "MVP"),
        dependencies=DependencySpec(),
        constraints=Constraints(),
    )
    defaults.update(overrides)
    return PluginIdea(**defaults)


def test_risk_flags_survive_verbatim_in_text_summary():
    idea = _idea()
    classification = classify(idea)
    assert classification.risk_flags, "fixture must actually produce a risk flag"
    summary = build_checkpoint(idea, classification, (), language="en")
    for flag in classification.risk_flags:
        assert flag in summary.text, "a risk flag was not carried verbatim into the checkpoint"


def test_low_confidence_gets_an_explicit_warning():
    idea = _idea(problem=ProblemStatement("something vague", "someone", "", "MVP"))
    classification = classify(idea)
    summary = build_checkpoint(idea, classification, (), language="en")
    if classification.confidence < 0.5:
        assert "LOW CONFIDENCE" in summary.text


def test_doc_files_are_listed_by_name():
    from pathlib import Path

    idea = _idea()
    classification = classify(idea)
    docs = (Path("/tmp/nonexistent/plugin-idea.md"),)
    summary = build_checkpoint(idea, classification, docs, language="en")
    assert "plugin-idea.md" in summary.text


def test_voice_text_defaults_to_plain_text_when_voice_unavailable(monkeypatch):
    import plugin_builder.checkpoint as checkpoint_mod

    monkeypatch.setattr(checkpoint_mod, "_VOICE_AVAILABLE", False)
    idea = _idea()
    classification = classify(idea)
    summary = build_checkpoint(idea, classification, (), language="en")
    assert summary.voice_polished is False
    assert summary.voice_text == summary.text


def test_unsupported_language_falls_back_to_plain_text():
    idea = _idea()
    classification = classify(idea)
    summary = build_checkpoint(idea, classification, (), language="fr")
    assert summary.voice_polished is False
    assert summary.language == "fr"
