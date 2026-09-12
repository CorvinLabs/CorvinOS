"""Voice Synthesizer Worker: Text-to-speech for video narration (Phase 2).

Stages:
1. Lexicon application (pronunciation mapping)
2. TTS engine integration (Azure/Google/Anthropic stub)
3. Lead-in/tail silence insertion
4. Timing measurement (measured, not estimated)
5. Per-scene audio file generation + metadata
6. SceneRenderedEvent emission (ADR-0314 feedback)
"""

from .synthesizer import VoiceSynthesizer

__all__ = ["VoiceSynthesizer"]
