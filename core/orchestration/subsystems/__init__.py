"""Remnant of the Brain-hub subsystem package (ADR-0347).

The hub, the brain and every subsystem were retired by ADR-2282 (2026-10-25):
none had a reachable production caller. Only ``notification_daemon.py`` is kept,
because ``core/skill_forge/skill_forge_v2.py`` (unreachable, outside ADR-2282's
scope) imports it at module level; it goes with a decision on that module.
"""

__all__: list[str] = []
