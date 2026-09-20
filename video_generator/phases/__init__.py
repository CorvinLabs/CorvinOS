"""
CorvinOS Video Generation Phases

Each phase is a self-contained video production step:
- Phase 1: Blender 3D Logo Animation
- Phase 2: Manim Architecture Diagrams
- Phase 3: SVG Data Flow Graphics
- Phase 4: Particle Effects
- Phase 5: Final Composition & Grading
"""

from .base_utils import (
    BasePhase,
    PhaseOutput,
    FFmpegHelper,
    VideoCompositor,
    ProgressTracker,
    setup_logging
)

from .phase1_blender_intro import BlenderIntroPhase
from .phase2_manim_diagrams import ManimDiagramsPhase
from .phase3_svg_flows import SVGFlowsPhase
from .phase4_particle_effects import ParticleEffectsPhase
from .phase5_compositor import CompositorPhase

__all__ = [
    'BasePhase',
    'PhaseOutput',
    'FFmpegHelper',
    'VideoCompositor',
    'ProgressTracker',
    'setup_logging',
    'BlenderIntroPhase',
    'ManimDiagramsPhase',
    'SVGFlowsPhase',
    'ParticleEffectsPhase',
    'CompositorPhase',
]
