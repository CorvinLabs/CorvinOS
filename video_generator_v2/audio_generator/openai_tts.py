#!/usr/bin/env python3
"""
OpenAI TTS Engine
Generates natural German speech using OpenAI's TTS API (curl-style, no pip)
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class OpenAITTSEngine:
    """Generate speech audio using OpenAI TTS API"""

    def __init__(self, api_key: Optional[str] = None, language: str = "de"):
        """
        Initialize TTS Engine

        Args:
            api_key: OpenAI API key (or read from OPENAI_API_KEY env var)
            language: Language code (de, en, fr, etc.)
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set in environment or parameters")

        self.language = language
        self.voice = "nova"  # High-quality natural voice
        self.model = "tts-1"
        self.speed = 1.0
        self.api_endpoint = "https://api.openai.com/v1/audio/speech"

        logger.info(f"✓ OpenAI TTS Engine initialized (voice={self.voice}, lang={language})")

    def generate_narration(
        self,
        text: str,
        output_file: str,
        segment_name: str = "narration"
    ) -> str:
        """
        Generate speech audio for text

        Args:
            text: German text to convert to speech
            output_file: Output MP3/WAV file path
            segment_name: Segment identifier for logging

        Returns:
            Path to generated audio file
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        start_time = datetime.now()

        logger.info(f"[{segment_name}] Generating speech: {len(text)} chars")

        # Create output directory
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        # Build curl command (no pip dependency)
        try:
            # Use curl to call OpenAI API directly
            headers = [
                f"Authorization: Bearer {self.api_key}",
                "Content-Type: application/json"
            ]

            payload = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "speed": self.speed
            }

            # Construct curl command
            cmd = ["curl", "-s", "-X", "POST"]
            for header in headers:
                cmd.extend(["-H", header])
            cmd.extend(["-d", json.dumps(payload)])
            cmd.extend(["--output", output_file])
            cmd.append(self.api_endpoint)

            logger.debug(f"[{segment_name}] Executing: {' '.join(cmd[:5])}...")

            # Execute curl
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                error_msg = result.stderr or "Unknown error"
                logger.error(f"[{segment_name}] API error: {error_msg}")
                raise RuntimeError(f"OpenAI TTS failed: {error_msg}")

            # Verify output file was created
            if not os.path.exists(output_file):
                raise RuntimeError(f"Output file not created: {output_file}")

            file_size = os.path.getsize(output_file)
            if file_size == 0:
                raise RuntimeError(f"Output file is empty: {output_file}")

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[{segment_name}] ✓ Generated {file_size} bytes in {duration:.1f}s")

            return output_file

        except subprocess.TimeoutExpired:
            logger.error(f"[{segment_name}] API timeout (>60s)")
            raise RuntimeError("OpenAI TTS timeout")
        except Exception as e:
            logger.error(f"[{segment_name}] Generation failed: {str(e)}")
            raise

    def convert_mp3_to_aac(
        self,
        input_file: str,
        output_file: str
    ) -> str:
        """
        Convert MP3 to AAC (for video compatibility)

        Args:
            input_file: MP3 input from OpenAI
            output_file: AAC output for video

        Returns:
            Path to converted audio file
        """
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        logger.info(f"Converting {input_file} → {output_file}")

        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-c:a", "aac",
            "-b:a", "192k",
            "-y",
            output_file
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning(f"FFmpeg warning: {result.stderr[:200]}")

            if not os.path.exists(output_file):
                raise RuntimeError(f"Conversion failed, output not created: {output_file}")

            file_size = os.path.getsize(output_file)
            logger.info(f"✓ Converted to AAC ({file_size} bytes)")

            return output_file
        except Exception as e:
            logger.error(f"Conversion failed: {str(e)}")
            raise

    def validate_audio_quality(self, audio_file: str) -> Dict[str, Any]:
        """
        Validate audio quality using ffmpeg

        Args:
            audio_file: Audio file to validate

        Returns:
            Quality metrics dict
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        logger.info(f"Validating audio: {audio_file}")

        metrics = {
            "file_size": os.path.getsize(audio_file),
            "valid": False,
            "duration": 0,
            "sample_rate": 0,
            "channels": 0,
            "mean_volume": 0,
            "max_volume": 0
        }

        # Get audio properties
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-of", "json",
                audio_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get("format"):
                    metrics["duration"] = float(data["format"].get("duration", 0))
                if data.get("streams"):
                    stream = data["streams"][0]
                    metrics["sample_rate"] = stream.get("sample_rate", 0)
                    metrics["channels"] = stream.get("channels", 0)
                    metrics["valid"] = True
        except Exception as e:
            logger.warning(f"Could not get audio properties: {e}")

        # Get volume stats using FFmpeg
        try:
            cmd = [
                "ffmpeg",
                "-i", audio_file,
                "-af", "volumedetect",
                "-f", "null",
                "-",
                "-v", "error"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            stderr = result.stderr or result.stdout
            if "mean_volume" in stderr:
                # Parse mean_volume: -35.2 dB
                for line in stderr.split('\n'):
                    if "mean_volume" in line:
                        try:
                            vol = float(line.split()[-2])
                            metrics["mean_volume"] = vol
                        except:
                            pass
                    if "max_volume" in line:
                        try:
                            vol = float(line.split()[-2])
                            metrics["max_volume"] = vol
                        except:
                            pass
        except Exception as e:
            logger.warning(f"Could not measure volume: {e}")

        # Validate
        metrics["is_audible"] = metrics["mean_volume"] > -30  # dB threshold
        logger.info(f"Audio Quality: duration={metrics['duration']:.1f}s, "
                   f"mean_vol={metrics['mean_volume']:.1f}dB, "
                   f"audible={metrics['is_audible']}")

        return metrics


def generate_german_narration(
    segments: list,
    output_dir: str = "/tmp/corvinos_voice",
    api_key: Optional[str] = None
) -> str:
    """
    Generate complete narration from segment list

    Args:
        segments: List of dicts with "text", "duration_seconds"
        output_dir: Output directory for audio files
        api_key: OpenAI API key

    Returns:
        Path to concatenated audio file (AAC)
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    engine = OpenAITTSEngine(api_key=api_key, language="de")

    audio_files = []
    for i, segment in enumerate(segments, 1):
        segment_name = segment.get("name", f"segment_{i}")
        text = segment.get("text", "")

        if not text:
            logger.warning(f"Segment {segment_name} has no text, skipping")
            continue

        # Generate MP3
        mp3_file = os.path.join(output_dir, f"{segment_name}.mp3")
        engine.generate_narration(text, mp3_file, segment_name=segment_name)

        # Convert to AAC
        aac_file = os.path.join(output_dir, f"{segment_name}.aac")
        engine.convert_mp3_to_aac(mp3_file, aac_file)

        # Validate
        metrics = engine.validate_audio_quality(aac_file)
        if not metrics["is_audible"]:
            logger.warning(f"Segment {segment_name} is not audible (vol={metrics['mean_volume']}dB)")

        audio_files.append({
            "file": aac_file,
            "name": segment_name,
            "duration": metrics["duration"]
        })

    if not audio_files:
        raise RuntimeError("No audio files generated")

    # Concatenate all audio files
    concat_file = os.path.join(output_dir, "concat_list.txt")
    with open(concat_file, 'w') as f:
        for item in audio_files:
            f.write(f"file '{item['file']}'\n")

    final_audio = os.path.join(output_dir, "narration_final.aac")
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-y",
        final_audio
    ]

    logger.info(f"Concatenating {len(audio_files)} audio segments...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        raise RuntimeError(f"Audio concatenation failed: {result.stderr}")

    logger.info(f"✓ Final narration: {final_audio}")
    return final_audio


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)

    segments = [
        {
            "name": "intro",
            "text": "Willkommen zu CorvinOS. Das ist die nächste Generation der KI-Infrastruktur.",
            "duration_seconds": 5
        },
        {
            "name": "features",
            "text": "Mit Sprachsteuerung, Verschlüsselung und vollständiger Audit-Chain.",
            "duration_seconds": 4
        }
    ]

    try:
        final_audio = generate_german_narration(segments)
        print(f"Generated: {final_audio}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
