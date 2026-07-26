"""Plugin registry records, dependency order and settings validation (ADR-0233).

Salvaged from the retired ``core/orchestration/plugin_system/models.py`` prototype
and reconciled with the ADR-0030 lifecycle contract in ``protocol.py``.

Deliberate differences from the prototype (see ADR-0233 § Findings):

* ``PluginRecord`` — not ``Plugin``.  A record is registry *metadata*; the runtime
  object is a ``CorvinPlugin`` (``protocol.py``).  One name per concept.
* ``plugin_type`` is validated against ``KNOWN_PLUGIN_TYPES`` instead of carrying a
  second, parallel taxonomy (the prototype's ``PluginType`` enum of
  skill/tool/engine/gate/compliance had no relation to the real extension points).
* ``origin`` (builtin | vetted | community) replaces the prototype's ``tier``.
  ADR-0233 D7: "tier" means ADR-0156's capability boundary repo-wide; provenance is
  a separate field.
* No ``quota``, ``marketplace`` or ``signature`` fields.  The prototype declared them
  while implementing none of the enforcing mechanism (no cgroups, no checksum check,
  no signature verification).  A field that promises a guarantee it cannot keep is
  worse than an absent field.
* ``to_dict``/``from_dict`` round-trip every persisted field.  The prototype dropped
  ``settings_schema``, ``settings_schema_version``, ``audit_required`` and the version
  history on save, silently losing them on the next load.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .protocol import (
    KNOWN_PLUGIN_TYPES,
    PluginAlreadyRegistered,
    PluginNotFound,
)

# ── Enums ─────────────────────────────────────────────────────────────────────


class PluginOrigin(str, Enum):
    """Where a plugin came from — provenance, NOT a capability tier.

    ADR-0233 D7: the Tier A/B/C vocabulary belongs to ADR-0156 (capability boundary
    plus license gate).  Provenance is orthogonal and lives here.
    """

    BUILTIN = "builtin"      # ships with CorvinOS
    VETTED = "vetted"        # reviewed by the maintainer
    COMMUNITY = "community"  # third-party, unreviewed


class PIIRisk(str, Enum):
    """Declared personal-data exposure of a plugin.  Gates the consent prompt."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UpdatePolicy(str, Enum):
    """How far an automatic update may move the version."""

    NONE = "none"    # manual only
    PATCH = "patch"  # 1.2.3 -> 1.2.9
    MINOR = "minor"  # 1.2.3 -> 1.9.0, never 2.0.0
    MAJOR = "major"  # any


# ── Exceptions ────────────────────────────────────────────────────────────────


class PluginError(Exception):
    """Base class for manifest/registry-record errors.

    Lookup and collision errors are NOT redefined here: ``PluginNotFound`` and
    ``PluginAlreadyRegistered`` are re-exported from ``protocol.py`` so that one
    concept has exactly one class.  A second ``PluginNotFound`` in this module
    would silently escape an ``except`` clause written against the other one.
    """


class DependencyConflictError(PluginError):
    """A dependency is missing or its version constraint is unsatisfiable."""


class CircularDependencyError(PluginError):
    """The dependency graph contains a cycle."""


class ValidationError(PluginError):
    """Settings do not satisfy the plugin's JSON Schema."""


class UnknownPluginType(PluginError):
    """plugin_type is not in KNOWN_PLUGIN_TYPES (protocol.py)."""


# ── Version constraints ───────────────────────────────────────────────────────

#: Operators recognised in a dependency spec, longest first so that ">=" is matched
#: before ">" and "<=" before "<".  Order is load-bearing.
_OPERATORS: tuple[str, ...] = (">=", "<=", "==", "!=", "~=", ">", "<")


@dataclass(frozen=True)
class PluginDependency:
    """A single dependency: a plugin_id plus an optional version constraint.

    An empty ``version_range`` means "any version".
    """

    plugin_id: str
    version_range: str = ""

    @classmethod
    def parse(cls, spec: str) -> PluginDependency:
        """Parse ``"postgres-tool>=1.0.0"`` into a dependency.

        A spec with no operator ("postgres-tool") means any version.
        """
        for op in _OPERATORS:
            if op in spec:
                plugin_id, _, constraint = spec.partition(op)
                return cls(plugin_id.strip(), op + constraint.strip())
        return cls(spec.strip(), "")

    def satisfies(self, other_version: str) -> bool:
        """True when ``other_version`` satisfies this constraint.

        The prototype returned False for every ``>`` and ``<`` constraint (it parsed
        them but never handled them) and carried an unreachable duplicate
        ``except`` clause.  Both are fixed here; a malformed constraint or version
        raises rather than silently evaluating to False, because a dependency that
        can never be satisfied must surface as an error, not as "not installed".
        """
        if not self.version_range:
            return True

        # Imported lazily: a malformed spec should raise DependencyConflictError,
        # not ImportError, on installs that lack the optional dependency.
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        spec = self.version_range
        if spec.endswith(".x"):
            # "1.x" / "1.2.x" -> the compatible-release form packaging spells "~=".
            # "~=" pins every component except the last, so the trailing ".0" is
            # what makes "1.x" pin the major (~=1.0) and "1.2.x" pin the minor
            # (~=1.2.0).  Without it, "1.2.x" becomes ~=1.2 and admits 1.3.0.
            spec = f"~={spec[:-2]}.0"

        try:
            return Version(other_version) in SpecifierSet(spec)
        except (InvalidSpecifier, InvalidVersion) as exc:
            raise DependencyConflictError(
                f"cannot evaluate constraint {self.version_range!r} "
                f"for {self.plugin_id!r}: {type(exc).__name__}"
            ) from exc

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.plugin_id}{self.version_range}"


# ── Breaking changes / schema migration ───────────────────────────────────────


@dataclass(frozen=True)
class BreakingChange:
    """One settings-schema change between two plugin versions.

    ``migration`` is a human-readable instruction shown to the operator.  It is
    never executed as code — ADR-0233 keeps migration under explicit approval
    (Marketplace-ADR open question #1 answered: user approval, not automatic).
    """

    old_setting: str
    new_setting: str
    migration: str = "copy value"


@dataclass(frozen=True)
class PluginManifest:
    """Metadata about one specific released version of a plugin."""

    plugin_id: str
    version: str
    settings_schema_version: str = "1.0"
    breaking_changes: tuple[BreakingChange, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "settings_schema_version": self.settings_schema_version,
            "breaking_changes": [
                {
                    "old_setting": bc.old_setting,
                    "new_setting": bc.new_setting,
                    "migration": bc.migration,
                }
                for bc in self.breaking_changes
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginManifest:
        return cls(
            plugin_id=data["plugin_id"],
            version=data["version"],
            settings_schema_version=data.get("settings_schema_version", "1.0"),
            breaking_changes=tuple(
                BreakingChange(
                    old_setting=bc["old_setting"],
                    new_setting=bc["new_setting"],
                    migration=bc.get("migration", "copy value"),
                )
                for bc in data.get("breaking_changes", ())
            ),
        )


def plan_settings_migration(
    old_settings: Dict[str, Any],
    changes: tuple[BreakingChange, ...] | List[BreakingChange],
) -> tuple[Dict[str, Any], List[str]]:
    """Propose migrated settings for a breaking upgrade.

    Returns ``(proposed_settings, notes)``.  The caller MUST present the notes and
    obtain explicit approval before persisting — this function never writes.
    Renames carry the old value across; a rename whose source key is absent is
    reported as a note and leaves the target untouched.
    """
    proposed = dict(old_settings)
    notes: List[str] = []
    for change in changes:
        if change.old_setting in proposed:
            proposed[change.new_setting] = proposed.pop(change.old_setting)
            notes.append(
                f"{change.old_setting} -> {change.new_setting} ({change.migration})"
            )
        else:
            notes.append(
                f"{change.old_setting} not present; {change.new_setting} left at default"
            )
    return proposed, notes


# ── The registry record ───────────────────────────────────────────────────────


@dataclass
class PluginRecord:
    """One entry in a tenant's plugin registry.

    This is metadata *about* a plugin, not the plugin itself.  The runtime object
    implements ``CorvinPlugin`` (``protocol.py``) and is created by the loader.
    """

    # Identity
    plugin_id: str
    version: str
    display_name: str
    plugin_type: str

    # Provenance / compliance declarations
    origin: PluginOrigin = PluginOrigin.COMMUNITY
    pii_risk: PIIRisk = PIIRisk.LOW
    requires_consent: bool = False
    audit_required: bool = True

    # Install / enable state
    installed_at: Optional[datetime] = None
    installed_by: Optional[str] = None
    enabled: bool = False
    enabled_at: Optional[datetime] = None
    update_policy: UpdatePolicy = UpdatePolicy.NONE

    # Settings
    settings: Dict[str, Any] = field(default_factory=dict)
    settings_schema: Dict[str, Any] = field(default_factory=dict)
    settings_schema_version: str = "1.0"

    # Wiring
    class_path: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    version_history: List[PluginManifest] = field(default_factory=list)

    # Last failure — exception class name only, never a message (no PII).
    last_error_type: Optional[str] = None
    error_count: int = 0

    def __post_init__(self) -> None:
        if self.plugin_type not in KNOWN_PLUGIN_TYPES:
            raise UnknownPluginType(
                f"plugin_type {self.plugin_type!r} is not a known extension point; "
                f"expected one of {sorted(KNOWN_PLUGIN_TYPES)}"
            )
        if not self.plugin_id:
            raise PluginError("plugin_id must not be empty")

    @property
    def full_id(self) -> str:
        """``"<plugin_id>/<version>"`` — the form used in audit events."""
        return f"{self.plugin_id}/{self.version}"

    def parsed_dependencies(self) -> List[PluginDependency]:
        return [PluginDependency.parse(spec) for spec in self.dependencies]

    def consent_required(self) -> bool:
        """True when this record may not be enabled without explicit consent.

        Deny-by-default shape: an explicit ``requires_consent`` OR a high PII risk
        OR community provenance all demand a prompt.  Callers must not narrow this.
        """
        return (
            self.requires_consent
            or self.pii_risk is PIIRisk.HIGH
            or self.origin is PluginOrigin.COMMUNITY
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise every persisted field (full round-trip, no silent loss)."""
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "display_name": self.display_name,
            "plugin_type": self.plugin_type,
            "origin": self.origin.value,
            "pii_risk": self.pii_risk.value,
            "requires_consent": self.requires_consent,
            "audit_required": self.audit_required,
            "installed_at": _iso(self.installed_at),
            "installed_by": self.installed_by,
            "enabled": self.enabled,
            "enabled_at": _iso(self.enabled_at),
            "update_policy": self.update_policy.value,
            "settings": self.settings,
            "settings_schema": self.settings_schema,
            "settings_schema_version": self.settings_schema_version,
            "class_path": self.class_path,
            "dependencies": list(self.dependencies),
            "version_history": [m.to_dict() for m in self.version_history],
            "last_error_type": self.last_error_type,
            "error_count": self.error_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginRecord:
        """Inverse of :meth:`to_dict`.  Unknown keys are rejected, not ignored.

        Fail-closed on shape: a registry written by a newer CorvinOS must not be
        half-read by an older one (that would silently drop state and then persist
        the truncated version back).
        """
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise PluginError(
                f"registry record for {data.get('plugin_id')!r} has unknown fields "
                f"{sorted(unknown)} — written by a newer version?"
            )
        try:
            return cls(
                plugin_id=data["plugin_id"],
                version=data["version"],
                display_name=data.get("display_name") or data["plugin_id"],
                plugin_type=data["plugin_type"],
                origin=PluginOrigin(data.get("origin", "community")),
                pii_risk=PIIRisk(data.get("pii_risk", "low")),
                requires_consent=bool(data.get("requires_consent", False)),
                audit_required=bool(data.get("audit_required", True)),
                installed_at=_parse_dt(data.get("installed_at")),
                installed_by=data.get("installed_by"),
                enabled=bool(data.get("enabled", False)),
                enabled_at=_parse_dt(data.get("enabled_at")),
                update_policy=UpdatePolicy(data.get("update_policy", "none")),
                settings=dict(data.get("settings") or {}),
                settings_schema=dict(data.get("settings_schema") or {}),
                settings_schema_version=data.get("settings_schema_version", "1.0"),
                class_path=data.get("class_path"),
                dependencies=list(data.get("dependencies") or []),
                version_history=[
                    PluginManifest.from_dict(m) for m in data.get("version_history") or []
                ],
                last_error_type=data.get("last_error_type"),
                error_count=int(data.get("error_count", 0)),
            )
        except KeyError as exc:
            raise PluginError(f"registry record missing required field {exc}") from exc

    def with_enabled(self, enabled: bool, *, now: Optional[datetime] = None) -> PluginRecord:
        """Return a copy with the enable state flipped (records are replaced, not mutated)."""
        stamp = now or datetime.now(timezone.utc)
        return replace(self, enabled=enabled, enabled_at=stamp if enabled else None)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ── Dependency order ──────────────────────────────────────────────────────────


class DependencyResolver:
    """Determines load order for a set of records via topological sort."""

    def __init__(self, records: Dict[str, PluginRecord]):
        self.records = records

    def load_order(self) -> List[str]:
        """Return plugin_ids in dependency order (dependencies first).

        Raises :class:`DependencyConflictError` for a missing or unsatisfiable
        dependency and :class:`CircularDependencyError` for a cycle.  Ties are
        broken alphabetically so the order is deterministic across runs — the
        prototype's dict-iteration order made the result depend on insert order.
        """
        dependents: Dict[str, List[str]] = {pid: [] for pid in self.records}
        in_degree: Dict[str, int] = {pid: 0 for pid in self.records}

        for pid, record in self.records.items():
            for dep in record.parsed_dependencies():
                if dep.plugin_id not in self.records:
                    raise DependencyConflictError(
                        f"{pid} depends on {dep.plugin_id}, which is not installed"
                    )
                installed = self.records[dep.plugin_id]
                if not dep.satisfies(installed.version):
                    raise DependencyConflictError(
                        f"{pid} requires {dep}, but "
                        f"{dep.plugin_id}=={installed.version} is installed"
                    )
                dependents[dep.plugin_id].append(pid)
                in_degree[pid] += 1

        queue = sorted(pid for pid, deg in in_degree.items() if deg == 0)
        order: List[str] = []
        while queue:
            pid = queue.pop(0)
            order.append(pid)
            newly_ready = []
            for dependent in dependents[pid]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    newly_ready.append(dependent)
            if newly_ready:
                queue = sorted(queue + newly_ready)

        if len(order) != len(self.records):
            stuck = sorted(set(self.records) - set(order))
            raise CircularDependencyError(
                f"circular dependency among {stuck}"
            )
        return order


# ── Settings validation ───────────────────────────────────────────────────────


class SettingsValidator:
    """Validates plugin settings against the plugin's JSON Schema."""

    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema or {}

    def validate(self, settings: Dict[str, Any]) -> bool:
        """True when settings satisfy the schema; raises :class:`ValidationError`.

        An empty schema accepts anything — a plugin without declared settings is a
        valid plugin.  When ``jsonschema`` is unavailable the required-keys subset
        is still enforced; that fallback is deliberately strict-by-omission rather
        than permissive, and it is logged by the caller.
        """
        if not self.schema:
            return True
        try:
            import jsonschema
        except ImportError:
            return self._validate_required_only(settings)

        try:
            jsonschema.validate(instance=settings, schema=self.schema)
        except jsonschema.ValidationError as exc:
            # exc.message names the offending key and constraint, never the value's
            # provenance; settings values themselves are operator-supplied config.
            raise ValidationError(f"settings rejected: {exc.message}") from exc
        except jsonschema.SchemaError as exc:
            raise ValidationError(f"plugin ships an invalid schema: {exc.message}") from exc
        return True

    def _validate_required_only(self, settings: Dict[str, Any]) -> bool:
        for key in self.schema.get("required", ()):
            if key not in settings:
                raise ValidationError(f"required setting missing: {key}")
        return True

    def defaults(self) -> Dict[str, Any]:
        """Collect declared top-level defaults, for pre-filling a settings form."""
        props = self.schema.get("properties") or {}
        return {
            key: spec["default"]
            for key, spec in props.items()
            if isinstance(spec, dict) and "default" in spec
        }


__all__ = [
    "BreakingChange",
    "CircularDependencyError",
    "DependencyConflictError",
    "DependencyResolver",
    "PIIRisk",
    "PluginAlreadyRegistered",
    "PluginDependency",
    "PluginError",
    "PluginManifest",
    "PluginNotFound",
    "PluginOrigin",
    "PluginRecord",
    "SettingsValidator",
    "UnknownPluginType",
    "UpdatePolicy",
    "ValidationError",
    "plan_settings_migration",
]
