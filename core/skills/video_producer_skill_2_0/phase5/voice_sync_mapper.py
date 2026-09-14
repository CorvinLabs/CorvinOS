"""Voice-Sync Mapper — Align Narration Timing to Animation Frames

Maps narration audio timing to animation keyframes for synchronized playback.
Ensures animation events happen when narrator mentions them.

ADR-0742: Didactic Storyboard System (Voice-Sync Timing)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import json


@dataclass
class Keyframe:
    """Animation keyframe tied to narration timing"""
    frame: int              # Frame index (frame = time_sec * 30 FPS)
    event: str              # "speech_start", "diagram_appears", etc.
    narrator_text: str      # Text spoken at this frame
    animation_action: Optional[str] = None  # "zoom_in", "highlight", etc.
    timestamp_sec: float = 0.0  # Absolute time in audio


@dataclass
class NarrationAudio:
    """Narration audio metadata"""
    audio_path: Path
    duration_sec: float
    frame_rate: int = 30  # Standard video frame rate


@dataclass
class VoiceSyncMapping:
    """Mapping of frames to narration events"""
    frame_to_event: Dict[int, str]      # {0: "Start", 120: "Feedback", ...}
    keyframes: List[Keyframe]            # Original keyframe list
    narrator_silence_ranges: List[Tuple[int, int]]  # (start_frame, end_frame)
    keyframe_indices: List[int]          # Which frames are anchor points

    def get_event_at_frame(self, frame: int) -> Optional[str]:
        """Get event at a specific frame"""
        return self.frame_to_event.get(frame)

    def is_silence(self, frame: int) -> bool:
        """Check if frame is in a silence range"""
        for start, end in self.narrator_silence_ranges:
            if start <= frame <= end:
                return True
        return False


class VoiceSyncMapper:
    """Maps narration timing to animation keyframes

    Example:
        audio = NarrationAudio(Path("narration.mp3"), duration_sec=30)
        keyframes = [
            Keyframe(frame=0, event="speech_start", narrator_text="Hello"),
            Keyframe(frame=60, event="diagram_appears", narrator_text="diagram"),
        ]
        mapper = VoiceSyncMapper()
        mapping = mapper.create_mapping(audio, keyframes)
    """

    def __init__(self, frame_rate: int = 30):
        """Initialize voice-sync mapper

        Args:
            frame_rate: Video frame rate (default: 30 FPS)
        """
        self.frame_rate = frame_rate

    def create_mapping(self, audio: NarrationAudio, keyframes: List[Keyframe]) -> VoiceSyncMapping:
        """Create voice-sync mapping from audio + keyframes

        Args:
            audio: NarrationAudio metadata
            keyframes: List of animation keyframes

        Returns:
            VoiceSyncMapping with frame-to-event mappings
        """

        # Validate keyframes
        total_frames = int(audio.duration_sec * self.frame_rate)
        for kf in keyframes:
            if kf.frame >= total_frames:
                raise ValueError(
                    f"Keyframe frame {kf.frame} exceeds audio duration "
                    f"({total_frames} frames @ {self.frame_rate} FPS)"
                )

        # Build frame-to-event mapping
        frame_to_event = {}
        for kf in keyframes:
            frame_to_event[kf.frame] = kf.event

        # Extract silence ranges (frames between consecutive narration events)
        keyframe_indices = [kf.frame for kf in keyframes]
        keyframe_indices.sort()

        silence_ranges = []
        # TODO: Implement silence detection from audio (for now, empty)

        return VoiceSyncMapping(
            frame_to_event=frame_to_event,
            keyframes=keyframes,
            narrator_silence_ranges=silence_ranges,
            keyframe_indices=keyframe_indices
        )

    def validate_mapping(self, mapping: VoiceSyncMapping, audio: NarrationAudio) -> List[str]:
        """Validate voice-sync mapping

        Args:
            mapping: VoiceSyncMapping to validate
            audio: NarrationAudio for reference

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        total_frames = int(audio.duration_sec * self.frame_rate)

        # Check keyframes are in order
        prev_frame = -1
        for kf in mapping.keyframes:
            if kf.frame <= prev_frame:
                errors.append(f"Keyframes not sorted: {kf.frame} <= {prev_frame}")
            prev_frame = kf.frame

        # Check keyframes don't exceed duration
        for kf in mapping.keyframes:
            if kf.frame >= total_frames:
                errors.append(
                    f"Keyframe {kf.frame} exceeds audio duration {total_frames}"
                )

        # Check for overlapping silence ranges
        ranges = mapping.narrator_silence_ranges
        for i, (start1, end1) in enumerate(ranges):
            for start2, end2 in ranges[i+1:]:
                if not (end1 < start2 or end2 < start1):
                    errors.append(
                        f"Overlapping silence ranges: ({start1}, {end1}) and ({start2}, {end2})"
                    )

        return errors

    def export_mapping_json(self, mapping: VoiceSyncMapping) -> str:
        """Export mapping as JSON

        Returns:
            JSON string representing the mapping
        """
        data = {
            "frame_to_event": {str(k): v for k, v in mapping.frame_to_event.items()},
            "keyframes": [
                {
                    "frame": kf.frame,
                    "event": kf.event,
                    "narrator_text": kf.narrator_text,
                    "animation_action": kf.animation_action,
                    "timestamp_sec": kf.timestamp_sec
                }
                for kf in mapping.keyframes
            ],
            "narrator_silence_ranges": mapping.narrator_silence_ranges,
            "keyframe_indices": mapping.keyframe_indices
        }
        return json.dumps(data, indent=2)

    def import_mapping_json(self, json_str: str) -> VoiceSyncMapping:
        """Import mapping from JSON

        Args:
            json_str: JSON string representing mapping

        Returns:
            VoiceSyncMapping
        """
        data = json.loads(json_str)

        keyframes = [
            Keyframe(
                frame=kf["frame"],
                event=kf["event"],
                narrator_text=kf["narrator_text"],
                animation_action=kf.get("animation_action"),
                timestamp_sec=kf.get("timestamp_sec", 0.0)
            )
            for kf in data["keyframes"]
        ]

        # Convert string keys back to int
        frame_to_event = {
            int(k): v for k, v in data["frame_to_event"].items()
        }

        return VoiceSyncMapping(
            frame_to_event=frame_to_event,
            keyframes=keyframes,
            narrator_silence_ranges=[
                tuple(r) for r in data.get("narrator_silence_ranges", [])
            ],
            keyframe_indices=data.get("keyframe_indices", [])
        )
