"""The Console as a bundled/builtin ``web_surface`` plugin (ADR-0356, P2.5).

The first instance of the ``web_surface`` plugin type: the CorvinOS Console SPA,
declared as a bundled/builtin plugin so it becomes a *replaceable* UI surface
rather than a fixed part of core (FrontendForge alternative UIs in P6, actual
loader-driven mount in P7).
"""
from .plugin import ConsolePlugin
from .registry_entries import declaration_entry

__all__ = ["ConsolePlugin", "declaration_entry"]
