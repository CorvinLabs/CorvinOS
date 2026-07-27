"""Document + scaffold generators (ADR-0253 Phase 4).

Four pure Markdown generators (``idea``, ``architecture``, ``adr``, ``plan``)
plus ``scaffold``, the one module that writes to disk / touches
``corvin_plugins``. Re-exported here so callers write
``from plugin_builder.generators import write_artifacts`` instead of reaching
into the submodule.
"""
from __future__ import annotations

from .adr import generate_adr_doc
from .architecture import generate_architecture_doc
from .idea import generate_idea_doc
from .plan import generate_build_plan_doc
from .scaffold import ScaffoldResult, slugify_plugin_id, write_artifacts

__all__ = [
    "generate_idea_doc",
    "generate_architecture_doc",
    "generate_adr_doc",
    "generate_build_plan_doc",
    "ScaffoldResult",
    "slugify_plugin_id",
    "write_artifacts",
]
