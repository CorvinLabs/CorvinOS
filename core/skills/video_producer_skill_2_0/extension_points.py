"""Marketplace Extension Points — Phase 3

Allow plugins to extend/override video generation.
"""

from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)

# Extension point registry
_extensions = {
    "spec_generator": None,
    "frame_renderer": None,
    "svg_renderer": None,           # Phase 4: SVG/diagram rendering
    "screencast_renderer": None,    # Phase 4: Screen capture + overlays
    "blender_renderer": None,       # Phase 4: 3D animation rendering
    "effects_processor": None,      # Phase 4: Transitions, color grading, animations
    "audio_renderer": None,
    "video_composer": None,
    "quality_scorer": None,
}

def register_extension(point: str, handler: Callable) -> bool:
    """Register extension for a plugin

    Args:
        point: Extension point name (spec_generator, frame_renderer, etc.)
        handler: Callable that implements the extension

    Returns:
        True if registered, False if point not found
    """
    if point not in _extensions:
        logger.warning(f"Unknown extension point: {point}")
        return False

    _extensions[point] = handler
    logger.info(f"✅ Extension registered: {point}")
    return True

def get_extension(point: str) -> Optional[Callable]:
    """Get registered extension handler"""
    return _extensions.get(point)

def call_extension(point: str, *args, **kwargs):
    """Call extension handler if registered, else use default

    Returns:
        Result from extension handler, or None if not registered
    """
    handler = get_extension(point)

    if handler is None:
        logger.debug(f"No extension registered for {point}, using default")
        return None

    try:
        logger.info(f"Calling extension: {point}")
        result = handler(*args, **kwargs)
        logger.info(f"✅ Extension succeeded: {point}")
        return result
    except Exception as e:
        logger.error(f"❌ Extension failed: {point} — {e}")
        raise

# Example extensions (for documentation)

def example_custom_spec_generator(brief: str, duration_sec: int, language: str = "de"):
    """Example: Custom LLM for spec generation

    Usage:
        from video_producer import extension_points
        extension_points.register_extension("spec_generator", example_custom_spec_generator)
    """
    logger.info(f"Custom spec generator called: {brief[:50]}...")
    # Custom LLM call or logic here
    return None

def example_custom_frame_renderer(spec, scene_idx: int):
    """Example: Custom PIL renderer with effects

    Usage:
        from video_producer import extension_points
        extension_points.register_extension("frame_renderer", example_custom_frame_renderer)
    """
    logger.info(f"Custom frame renderer called: scene {scene_idx}")
    # Custom rendering with custom fonts, effects, overlays
    return None

__all__ = [
    "register_extension",
    "get_extension",
    "call_extension",
]
