"""engine_policy.py — Phase 5 (ADR-0004) declarative engine routing.

Loads ``engine_policy.json`` from a workspace, validates its shape, and
exposes the resolution primitives the adapter needs to pick an engine
for a given compliance zone:

  * ``EnginePolicy.from_file(path) → EnginePolicy | None``
  * ``EnginePolicy.from_dict(data) → EnginePolicy`` (raises on invalid)
  * ``policy.default_chain() → list[str]``
  * ``policy.allow_engines_for(zone) → list[str]``
  * ``policy.validate_against_registry(known_ids) → list[str]``

Schema (subset documented in ADR-0004):

    {
      "default_engine":  "<engine_id>",        # required, str
      "fallback_chain":  ["<id>", "<id>"],     # optional, list[str]
      "compliance_zones": {                    # optional, mapping
        "<zone_name>": {
          "allow_engines": ["<id>", ...],      # optional, list[str]
          "deny_engines":  ["<id>", ...],      # optional, list[str]
          "audit_severity": "INFO|WARNING|ERROR|CRITICAL"  # optional
        }
      },
      "task_zone_classifier": "regex_pii"      # optional, str (advisory)
    }

Resolution rule:

  ``allow_engines_for(zone)`` returns the engines the policy permits
  for ``zone``, in deterministic order:
    1. Start with the zone's ``allow_engines`` (or all known engines
       from the default_chain when the allow list is missing/empty).
    2. Subtract anything in the zone's ``deny_engines``.
    3. Preserve insertion order — the first id is the operator's
       intended "best fit" for this zone.

  Unknown zone → returns ``default_chain()`` (the legacy path).

This module is **load-only**. Engine routing decisions and audit emission
live in the adapter; the policy module just exposes the shape so the
adapter can ask, "what's allowed here?" and degrade gracefully on
absence (returns None from `from_file`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZoneRule:
    """One compliance zone's engine permissions."""
    name: str
    allow_engines: tuple[str, ...] = ()
    deny_engines: tuple[str, ...] = ()
    audit_severity: str = "INFO"


@dataclass(frozen=True)
class EnginePolicy:
    """Declarative engine-routing policy for a workspace."""
    default_engine: str
    fallback_chain: tuple[str, ...] = ()
    zones: dict[str, ZoneRule] = field(default_factory=dict)
    task_zone_classifier: str = "regex_pii"

    # ----- factories ----------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> EnginePolicy | None:
        """Load + validate. Returns None when file is missing or
        unreadable. Raises ValueError when the file exists but is
        malformed — the adapter should treat that as a configuration
        error and surface it loudly."""
        p = Path(path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"engine_policy at {p}: {e}") from e
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnginePolicy:
        if not isinstance(data, dict):
            raise ValueError("engine_policy: top-level must be a dict")

        default_engine = data.get("default_engine")
        if not isinstance(default_engine, str) or not default_engine:
            raise ValueError(
                "engine_policy: 'default_engine' is required (non-empty string)"
            )

        fallback_chain_raw = data.get("fallback_chain") or []
        if not isinstance(fallback_chain_raw, list) or \
                not all(isinstance(x, str) for x in fallback_chain_raw):
            raise ValueError(
                "engine_policy: 'fallback_chain' must be a list of strings"
            )
        # Always ensure default_engine appears first in the effective
        # chain — operators sometimes forget to repeat it.
        chain = [default_engine] + [x for x in fallback_chain_raw
                                    if x != default_engine]

        zones_raw = data.get("compliance_zones") or {}
        if not isinstance(zones_raw, dict):
            raise ValueError(
                "engine_policy: 'compliance_zones' must be a dict"
            )

        zones: dict[str, ZoneRule] = {}
        for zone_name, zone_data in zones_raw.items():
            if not isinstance(zone_name, str) or not zone_name:
                raise ValueError(f"engine_policy: zone name must be non-empty string, got {zone_name!r}")
            if not isinstance(zone_data, dict):
                raise ValueError(f"engine_policy: zone {zone_name!r} must be a dict")
            allow = zone_data.get("allow_engines") or []
            deny = zone_data.get("deny_engines") or []
            sev = zone_data.get("audit_severity") or "INFO"
            if not isinstance(allow, list) or not all(isinstance(x, str) for x in allow):
                raise ValueError(f"engine_policy: zone {zone_name!r}.allow_engines must be list[str]")
            if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
                raise ValueError(f"engine_policy: zone {zone_name!r}.deny_engines must be list[str]")
            if sev not in ("INFO", "WARNING", "ERROR", "CRITICAL"):
                raise ValueError(f"engine_policy: zone {zone_name!r}.audit_severity must be INFO|WARNING|ERROR|CRITICAL")
            zones[zone_name] = ZoneRule(
                name=zone_name,
                allow_engines=tuple(allow),
                deny_engines=tuple(deny),
                audit_severity=sev,
            )

        classifier = data.get("task_zone_classifier") or "regex_pii"
        if not isinstance(classifier, str):
            raise ValueError("engine_policy: 'task_zone_classifier' must be string")

        return cls(
            default_engine=default_engine,
            fallback_chain=tuple(chain),
            zones=zones,
            task_zone_classifier=classifier,
        )

    # ----- query API ----------------------------------------------------

    def default_chain(self) -> list[str]:
        """Engine ids in operator-preferred fallback order, default first."""
        return list(self.fallback_chain)

    def allow_engines_for(self, zone: str | None) -> list[str]:
        """Engines permitted for the given zone, in operator order.

        - Unknown / None zone → returns ``default_chain()`` (legacy path).
        - Zone with empty ``allow_engines`` → starts from the full
          default_chain (catch-all behaviour), then applies deny.
        - Zone with explicit ``allow_engines`` → starts from that list,
          then applies deny.

        The result preserves the operator's intended order — first id
        is the best-fit engine for this zone.
        """
        if not zone or zone not in self.zones:
            return self.default_chain()
        rule = self.zones[zone]
        base = list(rule.allow_engines) if rule.allow_engines else self.default_chain()
        denied = set(rule.deny_engines)
        return [eid for eid in base if eid not in denied]

    def severity_for(self, zone: str | None) -> str:
        """Audit severity tag for events in this zone. Useful for
        operators who want PII-zone events to be visually distinct
        from code_only events in the audit chain."""
        if zone and zone in self.zones:
            return self.zones[zone].audit_severity
        return "INFO"

    def validate_against_registry(self, known_ids: list[str] | set[str]) -> list[str]:
        """Return warnings for engine_ids referenced in policy that the
        registry doesn't know. Catches typo'd ids in the JSON.

        Returns an empty list when everything checks out.
        """
        known = set(known_ids)
        warnings: list[str] = []

        if self.default_engine not in known:
            warnings.append(
                f"default_engine={self.default_engine!r} not in registry"
            )
        for eid in self.fallback_chain:
            if eid not in known:
                warnings.append(
                    f"fallback_chain references unknown engine_id={eid!r}"
                )
        for zname, rule in self.zones.items():
            for eid in rule.allow_engines:
                if eid not in known:
                    warnings.append(
                        f"zone {zname!r}.allow_engines references "
                        f"unknown engine_id={eid!r}"
                    )
            for eid in rule.deny_engines:
                if eid not in known:
                    warnings.append(
                        f"zone {zname!r}.deny_engines references "
                        f"unknown engine_id={eid!r}"
                    )
        return warnings

    def list_zones(self) -> list[str]:
        """All declared zones, sorted."""
        return sorted(self.zones.keys())
