"""PowerPoint Generator for video producer.

Uses pure Python implementation (no external dependencies) for PPTX generation.
"""

# Pure Python PPTX generator (no external dependencies)
from .generator_pure_python import PurePythonPPTXGenerator

# Legacy aliases for backward compatibility (if needed)
PowerPointGenerator = PurePythonPPTXGenerator

# For future use: high-level abstractions
BrandingConfig = None
SlideContent = None
AnimationSpec = None

__all__ = [
    "PurePythonPPTXGenerator",
    "PowerPointGenerator",
]
