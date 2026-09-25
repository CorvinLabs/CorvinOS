"""
Message Type Detection (Phase 2a)
Heuristic-based content type detection for type-aware summarization

Detects: Code, Images, Videos, Mixed content
Returns confidence scores for each type

@date 2026-09-25
@phase Phase 2a: Type-Aware Detection (Heuristic v1)
"""

import re
from typing import Dict, Tuple
from enum import Enum


class MessageType(str, Enum):
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    VIDEO = "video"
    MIXED = "mixed"


class TypeDetectionResult:
    def __init__(self, detected_type: MessageType, confidence: float, metadata: Dict = None):
        self.detected_type = detected_type
        self.confidence = confidence
        self.metadata = metadata or {}
        self.confidence_scores = {}  # Will be populated by detector


def detect_message_type(text: str, author_kind: str = "human") -> TypeDetectionResult:
    """
    Detect message content type using heuristic patterns.

    Strategy (Phase 2a - MVP):
    - Code: contains syntax keywords, indentation, common langs
    - Image: contains URLs ending in image extensions or [image] markers
    - Video: contains URLs ending in video extensions or [video] markers
    - Mixed: contains multiple types
    - Text: default

    Returns: TypeDetectionResult with confidence scores
    """

    scores = {
        "text": 0.0,
        "code": 0.0,
        "image": 0.0,
        "video": 0.0,
        "mixed": 0.0,
    }

    # Normalize text
    text_lower = text.lower()

    # ──────────────────────────────────────────────────────────
    # CODE DETECTION
    # ──────────────────────────────────────────────────────────

    code_score = 0.0
    code_metadata = {}

    # Language keywords
    code_keywords = {
        "def ": 0.3,  # Python function
        "class ": 0.3,  # Class definition
        "import ": 0.25,  # Import statement
        "function ": 0.2,  # JS/generic
        "const ": 0.25,  # JS
        "let ": 0.25,  # JS
        "var ": 0.2,  # Generic
        "return ": 0.25,  # Function body
        "if ": 0.15,  # Logic (low score, common in text)
        "for ": 0.15,  # Loop
        "while ": 0.15,  # Loop
        "switch ": 0.3,  # Distinctive
        "case ": 0.25,  # Switch case
        "try:": 0.3,  # Exception handling
        "except": 0.3,  # Python exception
        "catch": 0.3,  # JS exception
    }

    for keyword, weight in code_keywords.items():
        if keyword in text_lower:
            code_score += weight
            code_metadata[f"keyword_{keyword.strip()}"] = True

    # Indentation (very code-like)
    if re.search(r"^\s{4,}", text, re.MULTILINE):  # 4+ space indentation
        code_score += 0.3
        code_metadata["indentation"] = True

    # Curly braces / brackets (common in code)
    if text.count("{") + text.count("}") > 2:
        code_score += 0.2
        code_metadata["braces"] = True

    if text.count("[") + text.count("]") > 2:
        code_score += 0.15
        code_metadata["brackets"] = True

    # Code blocks (```language ... ```)
    if "```" in text:
        code_score += 0.8
        code_metadata["code_block"] = True
        # Extract language
        lang_match = re.search(r"```(\w+)?", text)
        if lang_match and lang_match.group(1):
            code_metadata["language"] = lang_match.group(1)

    scores["code"] = min(code_score, 1.0)

    # ──────────────────────────────────────────────────────────
    # IMAGE DETECTION
    # ──────────────────────────────────────────────────────────

    image_score = 0.0
    image_metadata = {}

    # Image file extensions
    image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]
    if any(ext in text_lower for ext in image_extensions):
        image_score += 0.7
        image_metadata["file_extension"] = True

    # Image URL patterns
    image_url_pattern = r"(https?://[^\s]+\.(jpg|jpeg|png|gif|webp|svg))"
    if re.search(image_url_pattern, text):
        image_score += 0.8
        image_metadata["url"] = True

    # [image] marker
    if "[image]" in text_lower or "<image>" in text_lower:
        image_score += 0.9
        image_metadata["marker"] = True

    scores["image"] = min(image_score, 1.0)

    # ──────────────────────────────────────────────────────────
    # VIDEO DETECTION
    # ──────────────────────────────────────────────────────────

    video_score = 0.0
    video_metadata = {}

    # Video file extensions
    video_extensions = [".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"]
    if any(ext in text_lower for ext in video_extensions):
        video_score += 0.7
        video_metadata["file_extension"] = True

    # Video URL patterns
    video_url_pattern = r"(https?://[^\s]+\.(mp4|mov|avi|mkv|webm|flv))"
    if re.search(video_url_pattern, text):
        video_score += 0.8
        video_metadata["url"] = True

    # [video] marker
    if "[video]" in text_lower or "<video>" in text_lower:
        video_score += 0.9
        video_metadata["marker"] = True

    # YouTube/Vimeo patterns
    if "youtube.com" in text_lower or "youtu.be" in text_lower or "vimeo.com" in text_lower:
        video_score += 0.8
        video_metadata["streaming_service"] = True

    scores["video"] = min(video_score, 1.0)

    # ──────────────────────────────────────────────────────────
    # DECIDE FINAL TYPE
    # ──────────────────────────────────────────────────────────

    # Check if mixed (multiple types detected)
    high_score_types = sum(1 for score in scores.values() if score > 0.4)

    if high_score_types > 1:
        detected_type = MessageType.MIXED
        confidence = max(scores.values())
    else:
        # Find highest score
        max_type = max(scores.items(), key=lambda x: x[1])[0]
        if scores[max_type] > 0.4:  # Threshold for detection
            detected_type = MessageType[max_type.upper()]
            confidence = scores[max_type]
        else:
            # Default to text
            detected_type = MessageType.TEXT
            confidence = 0.9 if scores["text"] == 0.0 else scores["text"]

    # Combine metadata
    metadata = {
        **code_metadata,
        **image_metadata,
        **video_metadata,
        "confidence_scores": scores,
    }

    result = TypeDetectionResult(detected_type, confidence, metadata)
    result.confidence_scores = scores
    return result


def get_summary_strategy_for_type(message_type: MessageType) -> str:
    """
    Map detected message type to summary strategy.

    Phase 2a: Simple strategies (Phase 2b: implement actual LLM strategies)
    """
    strategies = {
        MessageType.CODE: "syntax_aware",  # Preserve function signatures, key logic
        MessageType.IMAGE: "visual_aware",  # Describe objects, composition
        MessageType.VIDEO: "temporal_aware",  # Timeline, key scenes
        MessageType.MIXED: "entity_aware",  # Named entities, relations
        MessageType.TEXT: "simple",  # Default LLM summary
    }
    return strategies.get(message_type, "simple")
