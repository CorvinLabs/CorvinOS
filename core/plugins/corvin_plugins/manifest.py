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

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .protocol import (
    KNOWN_PLUGIN_TYPES,
    PluginAlreadyRegistered,
    PluginNotFound,
)

log = logging.getLogger("corvin.plugins.manifest")

# ── Enums ─────────────────────────────────────────────────────────────────────


class PluginOrigin(str, Enum):
    """Where a plugin came from — provenance, NOT a capability tier.

    ADR-0233 D7: the Tier A/B/C vocabulary belongs to ADR-0156 (capability boundary
    plus license gate).  Provenance is orthogonal and lives here.
    """

    BUILTIN = "builtin"      # ships with CorvinOS
    VETTED = "vetted"        # reviewed by the maintainer
    COMMUNITY = "community"  # third-party, unreviewed


class BootLayer(str, Enum):
    """Which boot stage a plugin belongs to — load order and disableability.

    ADR-0243 (formerly the second ADR-0234).  This is a THIRD axis, orthogonal to
    the two that already exist, and the naming is deliberate:

    * ``boot_layer`` — this one.  When is it loaded, may it be switched off?
    * ``tier``   — ADR-0156's capability boundary plus license gate.  The words
      "Tier A/B/C" mean that repo-wide and are NOT reused here (CLAUDE.md).
    * ``origin`` — provenance (builtin | vetted | community), see
      :class:`PluginOrigin`.  ADR-0233 D7.

    The draft ADRs spelled this axis "tier_0 / tier_1_core / tier_2_bundled",
    which collided head-on with both of the above.  Same concept, third meaning
    of the same word — so the word changed, not the concept.

    The ``boot_`` prefix carries the second half of that same lesson.  A bare
    ``layer`` was already taken four times over in this repo — the L1–L44
    architecture stack, ADR-0124 custom audit layers, the ADR-0142 layer
    extension API and the quality layers — so this axis, the newest and by far
    the smallest of the five, spells out which kind of layer it means.
    """

    COMPLIANCE = "compliance"  # GDPR / EU AI Act.  Never disableable, never replaceable.
    CORE = "core"              # bundled reference implementation.  Replaceable.
    BUNDLED = "bundled"        # ships with CorvinOS, opt-out per tenant (bridges, UI).
    INSTALLED = "installed"    # operator-installed third party.


#: Boot layers a plugin may NOT claim for itself unless it ships with CorvinOS or
#: was reviewed by the maintainer.  Without this, a community manifest could
#: declare ``boot_layer: compliance`` and thereby become (a) undisableable, (b)
#: loaded before everything else, and (c) exempt from the consent prompt path — a
#: privilege escalation via a single YAML line.
_PRIVILEGED_BOOT_LAYERS: frozenset[BootLayer] = frozenset(
    {BootLayer.COMPLIANCE, BootLayer.CORE}
)


class PIIRisk(str, Enum):
    """Declared personal-data exposure of a plugin.  Gates the consent prompt."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Locality(str, Enum):
    """Where the plugin's work physically happens (ADR-0124 Inv. 3, L34 vocabulary).

    Values match ``data_classification.Locality`` exactly — a second vocabulary for
    the same question would be a second answer to "may CONFIDENTIAL data reach this".
    """

    LOCAL = "local"        # runs on this machine / LAN
    EU_CLOUD = "eu_cloud"  # EU-jurisdiction infrastructure
    US_CLOUD = "us_cloud"  # US-jurisdiction infrastructure
    UNKNOWN = "unknown"    # not classified yet — treated as the least trusted


class NetworkEgress(str, Enum):
    """What the plugin talks to over the network (L34/L35 vocabulary)."""

    NONE = "none"          # no outbound calls at all
    LOCAL = "local"        # local sockets only
    EXTERNAL = "external"  # public internet


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


class InvalidPluginID(PluginError):
    """plugin_id fails the charset rule."""


# ── plugin_id charset ─────────────────────────────────────────────────────────

#: A plugin_id is used as a DIRECTORY NAME (per-plugin state dir) and lands in
#: audit details, log lines and the Console. It therefore gets an allowlist, not a
#: denylist — the same shape as ``validate_tenant_id``. A denylist on "/" alone
#: missed backslashes, which are path separators on Windows (a supported platform
#: per ADR-0159), so ``..\\..\\etc`` escaped the tenant directory and reached
#: ``shutil.rmtree`` on uninstall.
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Sequences that are never acceptable even inside the allowed charset.
_PLUGIN_ID_FORBIDDEN = ("..", "./", "/.")


def validate_plugin_id(plugin_id: object) -> str:
    """Return ``plugin_id`` when it is safe as an identifier AND a path segment.

    Raises :class:`InvalidPluginID` otherwise.  Lower-case only, must start
    alphanumeric, and no ``..`` anywhere — reverse-domain ids like
    ``com.example.my-notifier`` pass, path tricks do not.
    """
    if not isinstance(plugin_id, str):
        raise InvalidPluginID(
            f"plugin_id must be str, got {type(plugin_id).__name__}"
        )
    if not plugin_id:
        raise InvalidPluginID("plugin_id must not be empty")
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise InvalidPluginID(
            f"plugin_id {plugin_id!r} fails charset rule "
            f"[a-z0-9][a-z0-9._-]{{0,63}} (lower-case, no separators)"
        )
    for bad in _PLUGIN_ID_FORBIDDEN:
        if bad in plugin_id:
            raise InvalidPluginID(f"plugin_id {plugin_id!r} contains {bad!r}")
    return plugin_id


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

    # Boot layer (ADR-0243).  Defaults to the LEAST privileged value: a record
    # that does not say what it is gets loaded last and stays disableable.
    boot_layer: BootLayer = BootLayer.INSTALLED
    #: plugin_id of the ``boot_layer=core`` reference implementation this takes
    #: over from (ADR-0237 "full replacement").  ``None`` for the normal case.
    #: A compliance plugin can never be named here — see ``__post_init__``.
    replaces: Optional[str] = None

    # Provenance / compliance declarations
    origin: PluginOrigin = PluginOrigin.COMMUNITY
    pii_risk: PIIRisk = PIIRisk.LOW
    requires_consent: bool = False
    audit_required: bool = True
    #: ADR-0124 Inv. 3 — every extensible resource declares these, so L34/L35 can
    #: decide what data may reach it and whether it may talk to the network. The
    #: defaults are the LEAST trusted combination: an undeclared plugin is treated
    #: as unclassified with internet access, never as safe.
    locality: Locality = Locality.UNKNOWN
    network_egress: NetworkEgress = NetworkEgress.EXTERNAL
    #: Hosts the plugin declares it needs (L35). Empty with egress=external means
    #: "unrestricted", which the enable path refuses for community origin.
    egress_hosts: List[str] = field(default_factory=list)

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
        validate_plugin_id(self.plugin_id)
        # ADR-0124: "Accept a manifest with locality: cloud and network_egress:
        # none" is listed as a MUST NOT — the combination is self-contradictory,
        # and accepting it would let a cloud plugin claim air-gapped status.
        if (
            self.locality in (Locality.EU_CLOUD, Locality.US_CLOUD)
            and self.network_egress is NetworkEgress.NONE
        ):
            raise PluginError(
                f"{self.plugin_id}: locality={self.locality.value} contradicts "
                f"network_egress=none — a cloud plugin cannot be air-gapped"
            )
        # ADR-0243: a privileged boot layer is a claim about trust, so it may
        # only be claimed by something that shipped with CorvinOS or was
        # reviewed. A community manifest asserting `boot_layer: compliance`
        # would otherwise buy itself boot priority and permanent
        # undisableability for free.
        if (
            self.boot_layer in _PRIVILEGED_BOOT_LAYERS
            and self.origin is PluginOrigin.COMMUNITY
        ):
            raise PluginError(
                f"{self.plugin_id}: origin=community may not claim "
                f"boot_layer={self.boot_layer.value} — privileged boot layers "
                f"require origin=builtin or origin=vetted"
            )
        if self.replaces is not None:
            validate_plugin_id(self.replaces)
            if self.replaces == self.plugin_id:
                raise PluginError(f"{self.plugin_id}: cannot replace itself")
            # Compliance is the one boot layer with no replacement path at all —
            # the audit chain, the consent gate and the house-rules gate are not
            # pluggable (CLAUDE.md § Compliance Baseline). A plugin declaring
            # itself the replacement FOR compliance is refused here; that the
            # named target actually is a core plugin is re-checked by the
            # registry, the only place that knows the target's boot layer.
            if self.boot_layer is BootLayer.COMPLIANCE:
                raise PluginError(
                    f"{self.plugin_id}: boot_layer=compliance may not declare "
                    f"replaces — compliance mechanisms are not replaceable"
                )

    def can_disable(self) -> bool:
        """False for the compliance boot layer, True for every other one.

        The compliance boot layer carries the mechanisms CLAUDE.md marks as
        non-disableable (audit hash-chain, consent gate, path gate, house rules,
        flow guard). "Disable" for them is the same violation as an env kill-flag,
        so the answer is structural rather than configurable.
        """
        return self.boot_layer is not BootLayer.COMPLIANCE

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
            "boot_layer": self.boot_layer.value,
            "replaces": self.replaces,
            "origin": self.origin.value,
            "pii_risk": self.pii_risk.value,
            "requires_consent": self.requires_consent,
            "audit_required": self.audit_required,
            "locality": self.locality.value,
            "network_egress": self.network_egress.value,
            "egress_hosts": list(self.egress_hosts),
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
        # BACKWARD COMPATIBILITY (remove after 0.11): the boot-layer axis was
        # briefly persisted under the bare key ``layer`` before it was renamed to
        # ``boot_layer`` (the word was already taken four times over in this
        # repo).  A registry.yaml written in that window must not hit the
        # unknown-field guard below and take the whole tenant registry down, so
        # the legacy key is accepted, loudly, and re-persisted under the new name
        # on the next write.
        legacy_boot_layer = None
        if "layer" in data and "boot_layer" not in data:
            legacy_boot_layer = data.get("layer")
            log.warning(
                "registry record %r uses the legacy key 'layer' for the boot-layer "
                "axis — reading it as 'boot_layer'; it is rewritten on the next save",
                data.get("plugin_id"),
            )
        unknown = set(data) - known - {"layer"}
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
                # Absent boot_layer means "written before ADR-0243" — read as
                # the least privileged value, never as core.
                boot_layer=BootLayer(
                    data.get("boot_layer") or legacy_boot_layer or "installed"
                ),
                replaces=data.get("replaces") or None,
                origin=PluginOrigin(data.get("origin", "community")),
                pii_risk=PIIRisk(data.get("pii_risk", "low")),
                requires_consent=bool(data.get("requires_consent", False)),
                audit_required=bool(data.get("audit_required", True)),
                locality=Locality(data.get("locality", "unknown")),
                network_egress=NetworkEgress(data.get("network_egress", "external")),
                egress_hosts=list(data.get("egress_hosts") or []),
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
    "Locality",
    "NetworkEgress",
    "InvalidPluginID",
    "CircularDependencyError",
    "DependencyConflictError",
    "DependencyResolver",
    "PIIRisk",
    "PluginAlreadyRegistered",
    "PluginDependency",
    "PluginError",
    "BootLayer",
    "PluginManifest",
    "PluginNotFound",
    "PluginOrigin",
    "PluginRecord",
    "SettingsValidator",
    "UnknownPluginType",
    "UpdatePolicy",
    "ValidationError",
    "plan_settings_migration",
    "validate_plugin_id",
]
