"""
Type Detector Unit Tests (Phase 2a)
"""

import pytest
from core.console.corvin_console.services.type_detector import (
    detect_message_type,
    get_summary_strategy_for_type,
    MessageType,
)


class TestTypeDetectorPhase2a:
    """Phase 2a: Type-Aware Detection Heuristics"""

    def test_detect_code_python_function(self):
        """Test: Detect Python code"""
        text = """def route_request(task):
    if task.type == "complex":
        return dispatch(opus)
    return dispatch(haiku)"""
        result = detect_message_type(text)
        assert result.detected_type == MessageType.CODE
        assert result.confidence > 0.5

    def test_detect_code_javascript(self):
        """Test: Detect JavaScript code"""
        text = "const router = (req) => { if (req.method === 'GET') return handle(req); }"
        result = detect_message_type(text)
        assert result.detected_type == MessageType.CODE
        assert result.confidence > 0.4

    def test_detect_code_with_markers(self):
        """Test: Detect code blocks with ``` markers"""
        text = """Here's a Python example:
```python
def hello():
    return "world"
```"""
        result = detect_message_type(text)
        assert result.detected_type == MessageType.CODE
        assert result.confidence > 0.7
        assert "code_block" in result.metadata
        assert result.metadata.get("language") == "python"

    def test_detect_image_url(self):
        """Test: Detect image URLs"""
        text = "Check out this image: https://example.com/photo.jpg"
        result = detect_message_type(text)
        assert result.detected_type == MessageType.IMAGE
        assert result.confidence > 0.6

    def test_detect_image_marker(self):
        """Test: Detect [image] markers"""
        text = "Here's a visual example [image]"
        result = detect_message_type(text)
        assert result.detected_type == MessageType.IMAGE
        assert result.confidence > 0.8

    def test_detect_video_url(self):
        """Test: Detect video URLs"""
        text = "Watch this demo: https://example.com/demo.mp4"
        result = detect_message_type(text)
        assert result.detected_type == MessageType.VIDEO
        assert result.confidence > 0.6

    def test_detect_video_youtube(self):
        """Test: Detect YouTube links"""
        text = "Check out https://youtube.com/watch?v=abc123"
        result = detect_message_type(text)
        assert result.detected_type == MessageType.VIDEO
        assert result.confidence > 0.7

    def test_detect_text_default(self):
        """Test: Default to text for natural language"""
        text = "This is just a regular message with no code or media."
        result = detect_message_type(text)
        assert result.detected_type == MessageType.TEXT
        assert result.confidence > 0.8

    def test_detect_mixed_code_and_image(self):
        """Test: Detect mixed content"""
        text = """Here's the code:
```python
def display_image():
    return load("photo.jpg")
```
And here's the image: [image]"""
        result = detect_message_type(text)
        assert result.detected_type == MessageType.MIXED

    def test_summary_strategy_code(self):
        """Test: Code messages get syntax-aware strategy"""
        strategy = get_summary_strategy_for_type(MessageType.CODE)
        assert strategy == "syntax_aware"

    def test_summary_strategy_image(self):
        """Test: Image messages get visual-aware strategy"""
        strategy = get_summary_strategy_for_type(MessageType.IMAGE)
        assert strategy == "visual_aware"

    def test_summary_strategy_video(self):
        """Test: Video messages get temporal-aware strategy"""
        strategy = get_summary_strategy_for_type(MessageType.VIDEO)
        assert strategy == "temporal_aware"

    def test_confidence_scores_provided(self):
        """Test: All confidence scores returned"""
        text = "def hello(): return 'world'"
        result = detect_message_type(text)
        assert "confidence_scores" in result.metadata
        assert "code" in result.confidence_scores
        assert "image" in result.confidence_scores
        assert "video" in result.confidence_scores

    def test_indentation_detection(self):
        """Test: Code indentation recognized"""
        text = """
    def func():
        return value
    """
        result = detect_message_type(text)
        assert result.detected_type == MessageType.CODE
        assert "indentation" in result.metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
