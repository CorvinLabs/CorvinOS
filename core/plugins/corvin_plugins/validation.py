"""Offline plugin validation — the checks the registry runs, before boot (ADR-0247).

Every constraint a plugin must satisfy is enforced at exactly one moment today:
load time, in the running system. That is correct enforcement and none of it moves.
The problem is that it is the *only* enforcement, so an author's feedback loop is
"write code → install → boot CorvinOS → read a traceback", and the common case is
worse than a traceback: ``discover_and_load`` skips an unresolvable plugin at
``log.debug`` and ``auto_discover_entry_points`` defaults to ``False``, so a
correct plugin can simply never appear, with no signal at all.

**This module contains no copy of any rule.** It calls the real
``PluginRecord.__post_init__`` and registers into a throwaway ``PluginRegistry``.
A validator with its own reimplementation of the invariants drifts, and it drifts
in the worst direction — passing something the registry will reject, which is the
exact failure it was built to prevent.

Advisory only: passing grants nothing at load time, writes no attestation, and is
never consulted by the loader. The registry re-runs everything on every load.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .manifest import PluginError, PluginRecord
from .surface_map import surface_for

#: Severity levels. ``error`` means the registry WILL reject this plugin;
#: ``warning`` means it may load and still be wrong. The boundary is not tunable
#: — see ``ValidationReport.ok``.
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - display only
        mark = "ERROR" if self.level == ERROR else "warn "
        return f"{mark}  [{self.code}] {self.message}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def ok(self) -> bool:
        """True when nothing would make the registry reject this plugin.

        Warnings deliberately do not affect this. There is no ``--strict`` that
        promotes them and no ``--force`` that demotes errors: an override could
        only ever produce a plugin that passes validation and fails at boot,
        which inverts the purpose of the tool.
        """
        return not self.errors


# ── Individual checks ────────────────────────────────────────────────────────

#: Keys a plugin.yaml MANIFEST legitimately carries that are NOT persisted
#: registry-record fields. A manifest is a superset of a registry record: it
#: documents the plugin (``description``), declares its ADR-0599 capability
#: surface (``capabilities`` / ``capabilities_version`` / ``capabilities_note``)
#: and its ADR-0610/0611/0612 orchestration shape (``infrastructure_plugin`` /
#: ``orchestration_support``). ``PluginRecord.from_dict`` fails closed on unknown
#: keys — correct for ``registry.yaml`` round-tripping, where an unknown key
#: means "written by a newer version". But applying that verbatim to a manifest
#: FILE rejected every real marketplace manifest (all 30 builtins), which is why
#: ``bootstrap_builtin`` loaded zero of them and the console install gate could
#: not admit any builtin. Stripping only this explicit allowlist before the
#: record projection keeps the typo-catching (any OTHER unknown key still errors)
#: while accepting the legitimate manifest sections.
_MANIFEST_ONLY_KEYS: frozenset[str] = frozenset({
    "description",
    "capabilities",
    "capabilities_version",
    "capabilities_note",
    "infrastructure_plugin",
    "orchestration_support",
})


def strip_manifest_only_keys(data: dict) -> dict:
    """Return a copy of ``data`` without the manifest-only sections.

    Used to project a plugin.yaml manifest down to the registry-record fields
    ``PluginRecord.from_dict`` understands, without weakening its unknown-field
    guard for the registry round-trip.
    """
    return {k: v for k, v in data.items() if k not in _MANIFEST_ONLY_KEYS}


def validate_record_dict(data: dict) -> ValidationReport:
    """Validate a ``plugin.yaml``-shaped mapping via the real PluginRecord.

    Delegates wholesale to ``PluginRecord.from_dict`` → ``__post_init__``, which
    is where the locality/egress contradiction, the
    privileged-boot-layer-vs-origin refusal, and the ``replaces`` rules live.

    Manifest-only sections (``capabilities`` &c., see ``_MANIFEST_ONLY_KEYS``)
    are stripped first: a manifest is a superset of a registry record, and
    ``from_dict``'s fail-closed unknown-key guard is meant for the registry
    round-trip, not for the richer authored manifest.
    """
    report = ValidationReport()
    try:
        record = PluginRecord.from_dict(strip_manifest_only_keys(data))
    except PluginError as exc:
        report.add(ERROR, "record.invalid", str(exc))
        return report
    except KeyError as exc:
        report.add(ERROR, "record.missing_field", f"missing required field: {exc}")
        return report
    except (TypeError, ValueError) as exc:
        report.add(ERROR, "record.malformed", str(exc))
        return report

    _check_type_is_known(record, report)
    return report


def _check_type_is_known(record: PluginRecord, report: ValidationReport) -> None:
    """The declared plugin_type must be a surface that exists — and be reachable.

    The second half is the part that matters. A plugin whose type nothing
    consumes will load, register, report healthy and never be invoked. That is
    not an error — the plugin is well-formed and the registry accepts it — so it
    is a warning. But it must be a LOUD one, because it is invisible everywhere
    else in the system.
    """
    try:
        surface = surface_for(record.plugin_type)
    except KeyError as exc:
        report.add(ERROR, "type.unknown", str(exc))
        return

    if not surface.consumed:
        report.add(
            WARNING,
            "type.unconsumed",
            f"plugin_type {record.plugin_type!r} is not consumed by anything in "
            f"this build: {surface.dead_reason} — this plugin will load, "
            f"register, report healthy, and never be called.",
        )


def validate_class(
    cls: Any,
    *,
    expected_type: Optional[str] = None,
    expected_id: Optional[str] = None,
) -> ValidationReport:
    """Check a plugin class against the CorvinPlugin protocol.

    ``runtime_checkable`` protocols only verify method PRESENCE, not signatures,
    so this reports what it can honestly check and does not claim more.
    """
    report = ValidationReport()

    for attr in ("plugin_id", "plugin_type", "version", "display_name"):
        if not getattr(cls, attr, None):
            report.add(
                ERROR, "class.missing_attr", f"class does not define {attr!r}"
            )

    for method in ("on_load", "on_unload", "health_check"):
        if not callable(getattr(cls, method, None)):
            report.add(
                ERROR,
                "class.missing_method",
                f"class does not implement {method}() — required by CorvinPlugin",
            )

    declared = getattr(cls, "plugin_type", None)
    if expected_type and declared and declared != expected_type:
        report.add(
            ERROR,
            "class.type_mismatch",
            f"class declares plugin_type={declared!r} but the manifest says "
            f"{expected_type!r}",
        )

    declared_id = getattr(cls, "plugin_id", None)
    if expected_id and declared_id and declared_id != expected_id:
        # The id is the key for registration, for `spec.plugins.installed`, for
        # the consent record and for every audit event. When the manifest and the
        # class disagree, the class wins at runtime — so the manifest's compliance
        # declarations (pii_risk, requires_consent) end up filed under an id that
        # is never registered, and an operator declaring the manifest's id in
        # tenant.corvin.yaml configures a plugin that does not exist.
        report.add(
            ERROR,
            "class.id_mismatch",
            f"class declares plugin_id={declared_id!r} but the manifest says "
            f"{expected_id!r} — the class wins at registration, so the manifest "
            f"(and its pii_risk/requires_consent declarations) would describe an "
            f"id that never loads",
        )

    if declared:
        try:
            surface = surface_for(declared)
        except KeyError:
            pass  # already reported by the record check
        else:
            _check_registers_with_handle(cls, surface, report)

    return report


def _check_registers_with_handle(cls: Any, surface: Any, report: ValidationReport) -> None:
    """Warn when on_load() never mentions the handle it must register with.

    A heuristic, and reported as one: it reads the source of ``on_load`` looking
    for the ctx handle name. A plugin that registers indirectly will trip this
    and is not broken. Heuristics that block are how a validator becomes
    something authors route around, so this can only ever warn.

    Known limit: this is a substring scan over the method source, so the handle
    name counts as "mentioned" even when it appears only in a comment or a
    docstring. Tightening that would mean parsing the AST for a genuine attribute
    access, which is more machinery than a warning justifies — but it does mean
    the check can miss a plugin that talks about registering without doing it.
    """
    import inspect

    try:
        src = inspect.getsource(cls.on_load)
    except (OSError, TypeError):
        return
    if surface.ctx_handle not in src:
        report.add(
            WARNING,
            "class.no_self_registration",
            f"on_load() does not mention ctx.{surface.ctx_handle} — a plugin "
            f"that never registers is loaded and then ignored",
        )


def _sandbox_context(plugin_id: str, corvin_home: Path) -> Any:
    """A PluginContext whose audit sink goes nowhere.

    Deliberately NOT ``bootstrap.build_context``: that wires ``audit_emit`` to the
    real hash-chained trail. Validation is a build-time action on a developer's
    machine, not a system event — appending to a GDPR Art. 30 record from a linter
    would put developer noise into the audit chain (ADR-0247 § Compliance impact).

    The provider-registry handles are left ``None``. A plugin that guards its own
    registration (as every shipped template does) therefore skips it here, which is
    why ``validate_class`` inspects ``on_load`` separately rather than relying on
    an observable side effect.
    """
    from .protocol import PluginContext

    return PluginContext(
        plugin_id=plugin_id,
        tenant_id="_validation",
        corvin_home=corvin_home,
        config={},
        audit_emit=lambda _event, _details: None,
    )


def validate_registration(plugin: Any, *, corvin_home: Optional[Path] = None) -> ValidationReport:
    """Register the instance into a throwaway registry and confirm it lands.

    This is the check that cannot be faked by inspection: it uses the real
    ``PluginRegistry``, so whatever the registry would refuse, this refuses too.
    """
    import tempfile

    from .registry import PluginRegistry

    report = ValidationReport()
    plugin_id = getattr(plugin, "plugin_id", None)
    if not plugin_id:
        report.add(ERROR, "registry.no_plugin_id", "instance has no plugin_id")
        return report

    with tempfile.TemporaryDirectory(prefix="corvin-validate-") as tmp:
        home = corvin_home or Path(tmp)
        ctx = _sandbox_context(plugin_id, home)
        registry = PluginRegistry()
        try:
            registry.register(plugin, ctx)
        except Exception as exc:
            report.add(ERROR, "registry.refused", f"{type(exc).__name__}: {exc}")
            return report

        try:
            registry.get(plugin_id)
        except Exception as exc:  # pragma: no cover - would mean register() lied
            report.add(
                ERROR,
                "registry.not_retrievable",
                f"registered but not retrievable: {type(exc).__name__}: {exc}",
            )
    return report


def validate_discovery(
    *, has_entry_point: bool, has_class_path: bool, auto_discover: bool
) -> ValidationReport:
    """Check the plugin can actually be FOUND — the silent-failure path.

    ``discover_and_load`` skips a plugin with neither an entry point nor a
    ``class_path`` at ``log.debug``: the failure mode with no signal at all.

    It is nonetheless a WARNING, not an error, and the distinction is not
    cosmetic. ``class_path`` is declared in the operator's ``tenant.corvin.yaml``,
    not in the plugin — so from a plugin directory this function cannot know
    whether one exists. Reporting an error here would mean asserting knowledge the
    validator does not have, and it made every freshly scaffolded plugin fail its
    own ``corvin plugin check`` (found by running the new → check chain end to
    end). An error must mean "the registry WILL reject this"; this cannot.
    """
    report = ValidationReport()
    if not has_entry_point and not has_class_path:
        report.add(
            WARNING,
            "discovery.unreachable",
            "no 'corvin.plugins' entry point found in this directory — the "
            "plugin is loadable only if the operator declares a class_path in "
            "tenant.corvin.yaml. Without either, discover_and_load() skips it at "
            "debug level with no visible error",
        )
    if has_entry_point and not has_class_path and not auto_discover:
        report.add(
            WARNING,
            "discovery.entry_point_not_scanned",
            "this plugin is reachable only via its entry point, but "
            "spec.plugins.auto_discover_entry_points is false (the default) — "
            "it will not be loaded until that is enabled or a class_path is "
            "declared in tenant.corvin.yaml",
        )
    return report


# ── Composite ────────────────────────────────────────────────────────────────

def validate_manifest_file(path: Path) -> ValidationReport:
    """Validate a ``plugin.yaml`` on disk."""
    report = ValidationReport()
    if not path.is_file():
        report.add(ERROR, "manifest.missing", f"no such file: {path}")
        return report
    try:
        import yaml
    except ImportError:  # pragma: no cover
        report.add(ERROR, "manifest.no_yaml", "PyYAML not available")
        return report
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.add(ERROR, "manifest.unparseable", f"{type(exc).__name__}: {exc}")
        return report
    if not isinstance(data, dict):
        report.add(
            ERROR, "manifest.not_a_mapping", "plugin.yaml must contain a mapping"
        )
        return report
    return validate_record_dict(data)


def merge(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport()
    for r in reports:
        merged.findings.extend(r.findings)
    return merged


__all__ = [
    "ERROR",
    "WARNING",
    "Finding",
    "ValidationReport",
    "validate_record_dict",
    "validate_class",
    "validate_registration",
    "validate_discovery",
    "validate_manifest_file",
    "merge",
]
