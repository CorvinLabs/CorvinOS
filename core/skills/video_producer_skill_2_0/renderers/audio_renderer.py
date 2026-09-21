"""Audio Renderer (OpenAI TTS) — Phase 2

Synthesizes narration audio from text using OpenAI API.
"""

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class AudioRenderer:
    """OpenAI TTS audio renderer"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")

    def render_narration(self, text: str, language: str = "de", output_path: str = "/tmp/narration.mp3") -> str:
        """Synthesize narration audio using OpenAI TTS

        Args:
            text: Narration text
            language: Language code (de, en)
            output_path: Output MP3 file path

        Returns:
            Path to generated MP3 file
        """
        logger.info(f"🎙️ Generating TTS: {len(text)} chars ({language})")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": "tts-1-hd",
            "input": text,
            "voice": "nova",
            "speed": 1.0,
        }

        try:
            resp = requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers=headers,
                json=data,
                timeout=60,
            )

            if resp.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"✅ TTS generated: {len(resp.content)} bytes → {output_path}")
                return output_path
            else:
                logger.error(f"❌ TTS error: {resp.status_code} {resp.text[:200]}")
                raise RuntimeError(f"OpenAI TTS failed: {resp.status_code}")

        except Exception as e:
            logger.error(f"❌ TTS synthesis failed: {e}")
            raise

    def render_all_narrations(self, scenes: list, output_dir: str = "/tmp/audio") -> dict:
        """Render all scene narrations

        Args:
            scenes: List of Scene objects
            output_dir: Output directory for MP3 files

        Returns:
            Dict mapping scene_id → audio_file_path
        """
        os.makedirs(output_dir, exist_ok=True)
        results = {}

        for scene in scenes:
            if not scene.narration:
                logger.warning(f"Scene {scene.id}: no narration text")
                continue

            output_path = os.path.join(output_dir, f"{scene.id}_narration.mp3")
            audio_file = self.render_narration(scene.narration, output_path=output_path)
            results[scene.id] = audio_file

        logger.info(f"✅ All narrations rendered: {len(results)} scenes")
        return results
