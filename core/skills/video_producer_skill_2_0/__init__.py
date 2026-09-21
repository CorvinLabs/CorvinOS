"""Video Producer Skill 2.0 — Init + Extension Registration

Registers all 4 renderers at module import time.
"""

import logging

logger = logging.getLogger(__name__)


def _register_renderers():
    """Register all 4 renderers at boot time"""

    from video_producer_skill_2_0.extension_points import register_extension
    from video_producer_skill_2_0.renderers.svg_renderer import SvgRenderer
    from video_producer_skill_2_0.renderers.effects_processor import EffectsProcessor
    from video_producer_skill_2_0.renderers.screencast_renderer import ScreencastRenderer
    from video_producer_skill_2_0.renderers.blender_renderer import BlenderRenderer

    # Instantiate renderers
    svg = SvgRenderer()
    effects = EffectsProcessor()
    screencast = ScreencastRenderer()
    blender = BlenderRenderer()

    # Register extensions
    register_extension("svg_renderer", svg.render_flowchart)
    register_extension("effects_processor", effects.process_frames)
    register_extension("screencast_renderer", screencast.capture_and_compose)
    register_extension("blender_renderer", blender.render_scene)

    logger.info("✅ All 4 renderers registered at boot")


# Boot sequence
_register_renderers()

__all__ = [
    "SvgRenderer",
    "EffectsProcessor",
    "ScreencastRenderer",
    "BlenderRenderer",
]
