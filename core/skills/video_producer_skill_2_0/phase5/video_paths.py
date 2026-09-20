"""Where rendered video artifacts go.

Every Phase-5 renderer used to hard-wire ``/home/shumway/projects/Corvin-Videos``
as its output root. That path exists on exactly one machine: on any other
install — every Windows release, every container, every fresh ``install.sh`` —
the renderers wrote outside the runtime root or failed outright, and the
Video Producer surface is shipped code, not a maintainer script.

Resolution order:

1. ``CORVIN_VIDEO_ROOT`` — explicit operator override.
2. ``<corvin_home>/video`` — the canonical runtime root (``CORVIN_HOME``,
   else ``~/.corvin``), same resolver every other subsystem uses.

Nothing here creates directories; each renderer still owns when it does that.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.paths.tenant import corvin_home

#: Subdirectory of the video root per renderer tier.
BLENDER_OUTPUT = "blender_output"
MANIM_OUTPUT = "manim_output"
MANIM_SCENES = "scenes"
PREMIUM_OUTPUT = "premium_output"
QUICK_OUTPUT = "tier1_output"
THREEJS_OUTPUT = "threejs_output"


def video_root() -> Path:
    """Root directory for rendered video artifacts."""
    override = os.environ.get("CORVIN_VIDEO_ROOT")
    if override:
        return Path(override).expanduser()
    return corvin_home() / "video"


def video_dir(name: str) -> Path:
    """A named subdirectory under :func:`video_root`."""
    return video_root() / name
