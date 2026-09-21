"""
Audio Generator Module
OpenAI TTS and audio processing
"""

from .openai_tts import OpenAITTSEngine, generate_german_narration

__all__ = [
    "OpenAITTSEngine",
    "generate_german_narration",
]
