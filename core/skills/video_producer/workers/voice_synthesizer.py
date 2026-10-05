"""Voice Synthesizer Worker: Phase 4 Real TTS Integration

Synthesizes voice narration from text using real TTS APIs:
1. Split narration into scenes
2. Synthesize each scene to MP3 via OpenAI TTS (ADR-2211 default), falling
   back to edge-tts, then piper-tts, then a mock, in that order
3. Apply loudness normalization (-23 LUFS for broadcast standard)
4. Concatenate audio files
"""

from dataclasses import dataclass
from typing import List, Optional
import json
import os
import asyncio
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from .job_tmp import scene_path

# ADR-2211: OpenAI TTS is the default narration backend for every video this
# worker produces. "onyx" is OpenAI's calm, low male voice; speed is slowed
# slightly (platform default is 1.0) so narration reads as composed rather
# than rushed. Override via VoiceSynthesizerWorker(voice=...) / tts_provider=
# for a specific job, but these are the values every caller gets by default.
OPENAI_TTS_MODEL = "tts-1"
OPENAI_TTS_DEFAULT_VOICE = "onyx"
OPENAI_TTS_DEFAULT_SPEED = 0.92


@dataclass
class VoiceResult:
    """Voice synthesis result"""
    audio_files: List[str]
    total_duration_seconds: float
    loudness_lufs: float  # Target: -23 LUFS (broadcast standard)
    confidence: float
    success: bool = True
    provider_used: str = "unknown"  # "openai-tts" | "edge-tts" | "piper-tts" | "mock"


class VoiceSynthesizerWorker:
    """Worker Skill: Generate voice narration from text

    Phase 4 / ADR-2211: Real TTS, OpenAI first.
    Fallback chain per scene:
    - OpenAI TTS (tts-1, voice=onyx) - PRIMARY, needs OPENAI_API_KEY
    - edge-tts (free, no key) - fallback if no key or the API call fails
    - piper-tts - fallback if edge-tts unavailable
    - mock (JSON stand-in) - last resort, keeps the pipeline from hard-failing
    Plus: LUFS-based loudness normalization via FFmpeg, confidence scoring.
    """

    def __init__(
        self,
        tts_provider: str = "openai-tts",
        voice: str = OPENAI_TTS_DEFAULT_VOICE,
        speed: float = OPENAI_TTS_DEFAULT_SPEED,
        fallback_voice: str = "en-US-AvaMultilingualNeural",
    ):
        self.name = "voice_synthesizer"
        self.version = "4.1.0"  # ADR-2211: OpenAI TTS default + fallback chain
        self.tts_provider = tts_provider
        self.voice = voice
        self.speed = speed
        # Used only if we fall back to edge-tts (OpenAI and edge-tts voice
        # catalogues don't overlap — "onyx" means nothing to edge-tts).
        self.fallback_voice = fallback_voice

    def execute(self, job) -> VoiceResult:
        """Execute voice synthesis phase with REAL TTS

        Args:
            job: VideoJob instance

        Returns:
            VoiceResult with audio files and metadata
        """

        audio_files = []
        providers = []

        for i, scene_narration in enumerate(job.narration):
            # Synthesize scene to audio (REAL TTS, OpenAI first — ADR-2211)
            audio_path, provider = self._synthesize_scene_real(
                scene_narration, job_id=job.job_id, scene_index=i
            )
            audio_files.append(audio_path)
            providers.append(provider)

        if "mock" in providers:
            # A mock "audio" file is JSON. Reporting success let the job run on
            # until ASSEMBLY died on it; stop here and say which scenes failed.
            return VoiceResult(
                audio_files=audio_files, total_duration_seconds=0.0, loudness_lufs=0.0,
                confidence=0.0, success=False, provider_used=",".join(providers),
            )

        normalized = self._normalize_loudness_ffmpeg(audio_files, target_lufs=-23)

        # Durations AFTER normalization — that is the audio the video uses.
        total_duration = sum(self._get_audio_duration_ffprobe(a) for a in audio_files)

        # One provider -> its name; several -> the per-scene list, so a scene
        # that fell back is visible instead of hidden behind "mixed".
        provider_used = providers[0] if len(set(providers)) == 1 else ",".join(providers)

        return VoiceResult(
            audio_files=audio_files,
            total_duration_seconds=total_duration,
            loudness_lufs=-23.0 if normalized else 0.0,
            confidence=0.94,
            provider_used=provider_used,
        )

    def _synthesize_scene_openai(self, narration_text: str, output_path: str) -> bool:
        """Try OpenAI TTS for one scene. Returns True on success (writes
        output_path), False if no API key is configured or the call fails —
        callers fall back to edge-tts/piper/mock on False, they never raise.
        """
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CORVIN_TTS_OPENAI_KEY")
        if not api_key:
            return False

        try:
            payload = json.dumps(
                {
                    "model": OPENAI_TTS_MODEL,
                    "voice": self.voice,
                    "speed": self.speed,
                    "input": narration_text,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                "https://api.openai.com/v1/audio/speech",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    return False
                with open(output_path, "wb") as f:
                    f.write(response.read())
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return False

    def _synthesize_scene_real(
        self, narration_text: str, job_id: str, scene_index: int
    ) -> tuple[str, str]:
        """Synthesize a single scene, OpenAI TTS first (ADR-2211).

        Fallback chain: OpenAI TTS -> edge-tts -> piper-tts -> mock. Each
        step only runs if the previous one declined (no key) or failed.

        Args:
            narration_text: Text to synthesize
            job_id: Job identifier
            scene_index: Scene index

        Returns:
            (path to output MP3 audio file, provider name actually used)
        """

        output_path = scene_path(job_id, "scene", scene_index, ".mp3")

        if self._synthesize_scene_openai(narration_text, output_path):
            return output_path, "openai-tts"

        try:
            # Fallback: edge-tts (free, no API key)
            import edge_tts

            # Run async TTS in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def tts_async():
                    # Create TTS communication object
                    communicate = edge_tts.Communicate(
                        text=narration_text,
                        voice=self.fallback_voice,  # edge-tts voice, NOT self.voice (that's OpenAI's catalogue)
                        rate="+0%",  # edge-tts wants a signed percent string; 0.0 raised TypeError
                    )
                    # Save to MP3
                    await communicate.save(output_path)

                loop.run_until_complete(tts_async())
            finally:
                loop.close()

            if self._get_audio_duration_ffprobe(output_path) <= 0:
                raise RuntimeError("edge-tts wrote no playable audio")
            return output_path, "edge-tts"

        except Exception:
            # Fallback to piper-tts if edge-tts not available
            try:
                from piper.voice import PiperVoice

                # Use default piper model
                voice = PiperVoice.load("en_US-libritts-high")

                # Synthesize to WAV, then convert to MP3
                wav_path = output_path.replace(".mp3", ".wav")
                with open(wav_path, "wb") as wav_file:
                    voice.synthesize(narration_text, wav_file)

                # Convert WAV to MP3 using ffmpeg
                subprocess.run(
                    ["ffmpeg", "-y", "-i", wav_path, "-b:a", "192k", output_path],
                    check=True,
                    capture_output=True,
                )
                os.remove(wav_path)

                return output_path, "piper-tts"
            except Exception as e:
                # Fallback: create mock audio with duration estimation
                print(f"TTS failed: {e}, using mock")
                return self._synthesize_scene_mock(narration_text, job_id, scene_index), "mock"

    def _synthesize_scene_mock(
        self, narration_text: str, job_id: str, scene_index: int
    ) -> str:
        """Create mock audio for testing (fallback)

        Args:
            narration_text: Text to synthesize
            job_id: Job identifier
            scene_index: Scene index

        Returns:
            Path to output file (JSON metadata)
        """

        output_path = scene_path(job_id, "scene", scene_index, ".mp3")

        # Create metadata for mock audio
        metadata = {
            "narration": narration_text[:100],  # Truncate for storage
            "duration_estimate": len(narration_text) / 150.0,  # ~150 WPM
            "sample_rate": 48000,
            "bitrate": "128k",
            "format": "mp3",
            "loudness_lufs": -23,
        }

        # Write mock audio file with metadata
        with open(output_path, "w") as f:
            json.dump(metadata, f)

        return output_path

    def _get_audio_duration_ffprobe(self, audio_path: str) -> float:
        """Get duration of audio file using ffprobe — fail-open, real duration always.

        Phase 4: Parse ffprobe JSON output for precise audio duration. This directly
        affects frame-to-audio synchronization in video_assembler.py, so accuracy
        is load-bearing (a 1-second measurement error leaves video and narration
        desync by ~1s across the entire composition).

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds (never guessed)
        """

        try:
            # Never use mock duration estimates — even for testing, ffprobe on
            # a real MP3 is 1-2ms overhead. A 5s estimate that should be 90s
            # causes video/audio drift across the whole scene.
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass

        # Fail-open: if ffprobe fails, the audio file may be incomplete or
        # malformed, so log it and return 0 — callers will detect the broken
        # audio and can retry or fall back. Never guess.
        print(f"ffprobe failed on {audio_path}, duration unknown — may be incomplete")
        return 0.0

    def _normalize_loudness_ffmpeg(self, audio_files: List[str], target_lufs: float):
        """Normalize audio files to target loudness using FFmpeg loudnorm filter

        Phase 4: Real FFmpeg loudness normalization

        Broadcast standard: -23 LUFS
        Podcast standard: -14 LUFS
        YouTube standard: -4 LUFS LKFS

        Args:
            audio_files: List of audio file paths
            target_lufs: Target loudness in LUFS

        Returns:
            True only if every file was normalized.
        """

        all_ok = True
        for audio_file in audio_files:
            try:
                # Skip if it's a mock JSON file
                if not audio_file.endswith(".mp3") or os.path.getsize(audio_file) < 1000:
                    try:
                        with open(audio_file, "r") as f:
                            json.load(f)
                            continue  # Skip mock files
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Normalize loudness using FFmpeg loudnorm filter
                # loudnorm=I=-23:TP=-1.5:LRA=7 (EBU R128 standard)
                output_normalized = audio_file.replace(".mp3", "_normalized.mp3")

                cmd = [
                    "ffmpeg",
                    "-i", audio_file,
                    "-af", f"loudnorm=I={target_lufs}:TP=-1.5",
                    "-b:a", "192k",  # was -q:a 9: the LOWEST VBR quality (160 -> 41 kbps)
                    "-y",
                    output_normalized,
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    os.replace(output_normalized, audio_file)
                else:
                    all_ok = False

            except Exception as e:
                all_ok = False
                print(f"Loudness normalization failed for {audio_file}: {e}")
        return all_ok
