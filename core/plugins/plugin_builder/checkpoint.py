"""The ADR-0262 Zwischenstand checkpoint: text + voice summary of the
generated docs, shown before any scaffold code is written.

Two hard rules from ADR-0262, both enforced here rather than left to callers:

1. **Risk flags and low-confidence classification carry verbatim.** This
   project has already shipped and fixed exactly one voice-summary bug that
   silently dropped CRITICAL content on the way to a shorter spoken form —
   see the ``voice-summary-drops-critical-warnings`` project memory. This
   module reuses that lesson rather than re-deriving it: :func:`build_checkpoint`
   never paraphrases a risk flag, it only decides where each verbatim line
   goes (text summary vs. voice-polished summary), never whether it survives.
2. **Voice is a rendering, not a requirement.** ``voice_summary_smart``
   (``corvin_console``) is optional here — a console-only package that a
   test environment running just ``core/plugins`` won't have on the path.
   When it's unavailable, :class:`CheckpointSummary` says so explicitly
   (``voice_polished=False``) rather than silently returning the plain text
   dressed up as a "voice summary."
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .language import DEFAULT_LANGUAGE
from .models import Classification, PluginIdea

try:
    from corvin_console.voice_summary_smart import polish_for_audio

    _VOICE_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised by test_checkpoint_no_voice
    polish_for_audio = None  # type: ignore[assignment]
    _VOICE_AVAILABLE = False


@dataclass(frozen=True)
class CheckpointSummary:
    """The Zwischenstand presented before scaffold + test generation."""

    #: Full text summary — always complete, always includes every risk flag
    #: verbatim. Correct for a console/text transport as-is.
    text: str
    #: The same content run through ``polish_for_audio`` for a spoken
    #: transport, when available. Equal to ``text`` when it isn't.
    voice_text: str
    #: True iff ``voice_text`` actually went through TTS-oriented polishing.
    #: A caller that needs to KNOW whether voice rendering happened (rather
    #: than silently accept plain text) reads this, not string equality.
    voice_polished: bool
    language: str


def build_checkpoint(
    idea: PluginIdea,
    classification: Classification,
    doc_files: tuple[Path, ...],
    *,
    language: str = DEFAULT_LANGUAGE,
) -> CheckpointSummary:
    """Assemble the checkpoint the user reviews before any code is written.

    ``doc_files`` should be exactly what :func:`generators.write_idea_docs`
    just wrote — this function reads none of their content back (the prose
    below is composed fresh from ``idea``/``classification``, the same
    source data the docs themselves were generated from); it only lists
    their names so the user knows what to go read.
    """
    lines = [
        f"Idea reviewed: {idea.plugin_name}",
        "",
        f"Problem: {idea.problem.problem}",
    ]
    if idea.problem.target_audience and "not specified" not in idea.problem.target_audience:
        lines.append(f"Audience: {idea.problem.target_audience}")
    lines += [
        "",
        f"Classification: {classification.kind.value} "
        f"(Tier {classification.tier.value}, "
        f"confidence {classification.confidence:.0%})",
        classification.rationale,
    ]
    if classification.plugin_type:
        lines.append(f"Plugin type: {classification.plugin_type}")

    # Verbatim — every risk flag, in full, no truncation, no paraphrase.
    for flag in classification.risk_flags:
        lines.append(f"RISK: {flag}")
    if classification.confidence < 0.5:
        lines.append(
            f"LOW CONFIDENCE ({classification.confidence:.0%}) — the "
            "classification above is a best guess; review it carefully "
            "before continuing."
        )

    if doc_files:
        lines.append("")
        lines.append("Generated for review:")
        lines.extend(f"- {p.name}" for p in doc_files)

    lines += [
        "",
        "Nothing beyond these documents has been written yet. Reply "
        "confirm to generate the code scaffold and tests, restart to redo "
        "the interview, or cancel to stop here.",
    ]
    text = "\n".join(lines)

    if _VOICE_AVAILABLE and language in ("de", "en"):
        voice_text = polish_for_audio(text, lang=language)  # type: ignore[misc]
        voice_polished = True
    else:
        voice_text = text
        voice_polished = False

    return CheckpointSummary(
        text=text,
        voice_text=voice_text,
        voice_polished=voice_polished,
        language=language,
    )


__all__ = ["CheckpointSummary", "build_checkpoint"]
