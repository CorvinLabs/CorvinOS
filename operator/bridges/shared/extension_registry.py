"""ADR-0142 M2 + M3 — Extension Registry + hook execution pipeline.

Responsibilities
================
M2 — load + validate ``layer.yaml`` manifests, enforce the namespace gate
     (reject ``corvin.*``), resolve extensions across the five ADR-0007 scopes
     in the documented resolution order, and fail-to-load when a declared
     ``requires: corvin.* >= X`` core capability is absent or under-version.

M3 — the hook execution pipeline: ``run_pre_tool_use(tool_name, tool_input,
     ctx)`` runs core hooks first (priority 0) then extension hooks (priority
     desc, then name asc). Deny-wins: ALL hooks run, the final verdict is deny
     if ANY hook denied. An extension can never un-deny a core deny.

Constraints (load-bearing — CLAUDE.md / ADR-0142):
  * NO ``import anthropic`` (CI AST lint enforces).
  * Namespace gate is CRITICAL: a name starting with ``corvin.``, lacking a
    ``.`` separator, or violating ``[a-z0-9_][a-z0-9._-]{0,127}`` is rejected
    at load with an ``ext.core_namespace_rejected`` audit event.
  * Core dependency missing => fail-to-load (``ext.load_failed``), never a
    silent degrade.

Core-capability seam
====================
The authoritative core-capability registry is ADR-0141's
``security_capabilities.py`` (a parallel task — do NOT import it here, to keep
the two ADRs decoupled). Instead callers may inject a ``core_capabilities``
mapping ``{capability_name: version}``; we default to a safe built-in set of the
8 core layers with conservative versions. When ADR-0141 lands, the production
wiring passes its registry through this seam without changing this module.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from . import extension_api as _ext_api  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import extension_api as _ext_api  # type: ignore

ExtensionHook = _ext_api.ExtensionHook
HookResult = _ext_api.HookResult
HookContext = _ext_api.HookContext

# ── naming / namespace gate ──────────────────────────────────────────────────
_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9._-]{0,127}$")
_CORE_PREFIX = "corvin."

# ── five-scope model (ADR-0007 / ADR-0142 storage table) ─────────────────────
# Resolution order: most-specific first. For `provides`, most-specific wins; for
# `hooks`, ALL matching-scope extensions run (deny-wins applies regardless).
SCOPES: tuple[str, ...] = ("session", "task", "project", "user", "tenant")

# ── default core-capability set (the seam; ADR-0141 overrides at wiring) ─────
# Conservative built-in versions of the 8 mandatory core layers. The keys are
# the `corvin.*` capability names an extension may `requires:`.
DEFAULT_CORE_CAPABILITIES: dict[str, str] = {
    "corvin.path_gate": "2.1",
    "corvin.audit": "3.0",
    "corvin.consent_gate": "1.4",
    "corvin.data_class": "1.0",
    "corvin.egress_gate": "1.1",
    "corvin.disclosure": "1.0",
    "corvin.session_reset": "1.0",
    "corvin.dialectic": "1.0",
}

# Human-readable description of each core layer for `corvin-layer list`.
CORE_LAYER_DESC: dict[str, str] = {
    "corvin.path_gate": "L10 FS-write protection",
    "corvin.audit": "L16 hash-chain audit",
    "corvin.consent_gate": "L16 Ph4 GDPR consent",
    "corvin.data_class": "L34 data classification",
    "corvin.egress_gate": "L35 network egress",
    "corvin.disclosure": "L19 bot-disclosure card",
    "corvin.session_reset": "L8 session lifecycle",
    "corvin.dialectic": "L11 dialectic decision-points",
}


class ExtensionError(Exception):
    """Base class for extension-registry errors."""


class ExtensionManifestError(ExtensionError):
    """Raised when a ``layer.yaml`` is malformed or fails schema validation."""


class ExtensionNamespaceError(ExtensionError):
    """Raised when an extension name violates the namespace gate."""


class ExtensionDependencyError(ExtensionError):
    """Raised when a declared core dependency is absent or under-version."""


# ── version helpers (minimal semver-ish: dotted numeric compare) ─────────────
def _parse_version(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v).strip().split("."):
        m = re.match(r"^(\d+)", chunk)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts) or (0,)


def _version_ge(have: str, want: str) -> bool:
    a, b = _parse_version(have), _parse_version(want)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a >= b


_REQUIRES_RE = re.compile(
    r"^\s*([A-Za-z0-9_.\-]+)\s*(?:(>=|==|>)\s*([0-9][0-9A-Za-z.\-]*))?\s*$"
)


def _parse_requires(spec: Any) -> tuple[str, str, str]:
    """Parse a ``requires`` entry. Accepts a string ``"corvin.audit >= 1.0"``
    or a dict ``{name, version}``. Returns ``(name, op, version)`` with op in
    {"", ">=", "==", ">"} and version "" when unconstrained."""
    if isinstance(spec, dict):
        name = str(spec.get("name", "")).strip()
        version = str(spec.get("version", "")).strip()
        op = str(spec.get("op", ">=")).strip() if version else ""
        if not name:
            raise ExtensionManifestError("requires entry missing 'name'")
        return name, op, version
    if isinstance(spec, str):
        m = _REQUIRES_RE.match(spec)
        if not m:
            raise ExtensionManifestError(f"malformed requires entry: {spec!r}")
        name = m.group(1)
        op = m.group(2) or ""
        version = m.group(3) or ""
        return name, op, version
    raise ExtensionManifestError(f"requires entry must be str or dict, got {type(spec).__name__}")


# ── manifest model ───────────────────────────────────────────────────────────
@dataclass
class HookDecl:
    event: str
    script: str
    priority: int = 0


@dataclass
class ExtensionManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    scope: str = "tenant"
    hooks: list[HookDecl] = field(default_factory=list)
    provides: list[dict[str, Any]] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)
    # Runtime state (not part of the on-disk manifest).
    enabled: bool = False
    root: Path | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "scope": self.scope,
            "hooks": [{"event": h.event, "script": h.script, "priority": h.priority} for h in self.hooks],
            "provides": self.provides,
            "requires": self.requires,
            "mcp_tools": self.mcp_tools,
            "enabled": self.enabled,
        }


def validate_name(name: str) -> str:
    """Enforce the ADR-0142 namespace gate. Raises ExtensionNamespaceError on
    violation. Returns the name unchanged when valid."""
    if not isinstance(name, str) or not name:
        raise ExtensionNamespaceError("extension name must be a non-empty string")
    if name.startswith(_CORE_PREFIX):
        raise ExtensionNamespaceError(
            f"'{name}' uses the reserved 'corvin.' namespace (core layers only)"
        )
    if "." not in name:
        raise ExtensionNamespaceError(
            f"'{name}' must contain a '.' separator (vendor.extension convention)"
        )
    if not _NAME_RE.match(name):
        raise ExtensionNamespaceError(
            f"'{name}' fails charset rule [a-z0-9_][a-z0-9._-]{{0,127}}"
        )
    return name


def parse_manifest(data: dict[str, Any], *, root: Path | None = None) -> ExtensionManifest:
    """Validate a parsed ``layer.yaml`` mapping into an ExtensionManifest.

    Does NOT run the namespace gate (callers run validate_name separately so a
    rejection can emit ext.core_namespace_rejected). Raises
    ExtensionManifestError on schema problems.
    """
    if not isinstance(data, dict):
        raise ExtensionManifestError("layer.yaml must be a mapping at the top level")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ExtensionManifestError("layer.yaml missing required field 'name'")
    name = name.strip()

    version = data.get("version")
    if version is None:
        raise ExtensionManifestError("layer.yaml missing required field 'version'")
    version = str(version).strip()

    scope = str(data.get("scope", "tenant")).strip()
    if scope not in SCOPES:
        raise ExtensionManifestError(
            f"scope {scope!r} must be one of {sorted(SCOPES)}"
        )

    hooks_raw = data.get("hooks", []) or []
    if not isinstance(hooks_raw, list):
        raise ExtensionManifestError("'hooks' must be a list")
    hooks: list[HookDecl] = []
    for entry in hooks_raw:
        if not isinstance(entry, dict):
            raise ExtensionManifestError("each hooks entry must be a mapping")
        event = entry.get("event")
        script = entry.get("script")
        if not isinstance(event, str) or not event.strip():
            raise ExtensionManifestError("hooks entry missing 'event'")
        if not isinstance(script, str) or not script.strip():
            raise ExtensionManifestError("hooks entry missing 'script'")
        try:
            priority = int(entry.get("priority", 0))
        except (TypeError, ValueError):
            raise ExtensionManifestError("hooks entry 'priority' must be an integer")
        hooks.append(HookDecl(event=event.strip(), script=script.strip(), priority=priority))

    provides_raw = data.get("provides", []) or []
    if not isinstance(provides_raw, list):
        raise ExtensionManifestError("'provides' must be a list")
    provides: list[dict[str, Any]] = []
    for entry in provides_raw:
        if isinstance(entry, dict) and "name" in entry:
            provides.append({"name": str(entry["name"]), "version": str(entry.get("version", version))})
        elif isinstance(entry, str):
            provides.append({"name": entry, "version": version})
        else:
            raise ExtensionManifestError("each provides entry must be a mapping with 'name' or a string")

    requires_raw = data.get("requires", []) or []
    if not isinstance(requires_raw, list):
        raise ExtensionManifestError("'requires' must be a list")
    requires: list[str] = []
    for entry in requires_raw:
        if isinstance(entry, str):
            requires.append(entry)
        elif isinstance(entry, dict):
            nm = str(entry.get("name", "")).strip()
            ver = str(entry.get("version", "")).strip()
            requires.append(f"{nm} >= {ver}" if ver else nm)
        else:
            raise ExtensionManifestError("each requires entry must be a string or mapping")

    mcp_tools_raw = data.get("mcp_tools", []) or []
    if not isinstance(mcp_tools_raw, list):
        raise ExtensionManifestError("'mcp_tools' must be a list")

    return ExtensionManifest(
        name=name,
        version=version,
        description=str(data.get("description", "")),
        author=str(data.get("author", "")),
        license=str(data.get("license", "")),
        scope=scope,
        hooks=hooks,
        provides=provides,
        requires=requires,
        mcp_tools=list(mcp_tools_raw),
        root=root,
    )


def check_requires(
    manifest: ExtensionManifest,
    core_capabilities: dict[str, str],
) -> None:
    """Fail-to-load if any declared core ``requires`` is absent/under-version.

    Per ADR-0142: a missing core dependency MUST fail the load, never degrade
    silently. Raises ExtensionDependencyError on the first unmet dependency.
    Non-core (``requires`` of another extension) are checked for presence too
    when a provider set is supplied; absent non-core deps also fail (fail-to-load
    is the documented contract).
    """
    for spec in manifest.requires:
        name, op, want = _parse_requires(spec)
        have = core_capabilities.get(name)
        if have is None:
            raise ExtensionDependencyError(
                f"required capability '{name}' is not available"
            )
        if want and op in (">=", ">", "=="):
            if op == ">=" and not _version_ge(have, want):
                raise ExtensionDependencyError(
                    f"required '{name} >= {want}' but only {have} is available"
                )
            if op == ">" and not (_version_ge(have, want) and have != want):
                raise ExtensionDependencyError(
                    f"required '{name} > {want}' but only {have} is available"
                )
            if op == "==" and have != want:
                raise ExtensionDependencyError(
                    f"required '{name} == {want}' but {have} is available"
                )


# ── registry ─────────────────────────────────────────────────────────────────
class ExtensionRegistry:
    """Loads, validates, and resolves extensions across the five scopes, and
    runs the deny-wins hook pipeline.

    Storage roots are resolved lazily from ``corvin_home`` / ``tenant_home``
    (env ``CORVIN_HOME`` redirects, as tests rely on). ``project_root`` defaults
    to the cwd's ``.corvin`` tree.
    """

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        session_id: str = "",
        project_root: Path | None = None,
        core_capabilities: dict[str, str] | None = None,
        core_hooks: "list[tuple[int, str, ExtensionHook]] | None" = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._session_id = session_id
        self._project_root = project_root
        self.core_capabilities = dict(core_capabilities or DEFAULT_CORE_CAPABILITIES)
        # Core hooks: list of (priority, name, hook). priority is informational —
        # core always runs first regardless. Default empty: callers (the adapter)
        # inject the live core hooks; tests inject fakes.
        self._core_hooks: list[tuple[int, str, ExtensionHook]] = list(core_hooks or [])
        # name -> ExtensionManifest (loaded, possibly disabled)
        self._extensions: dict[str, ExtensionManifest] = {}
        # name -> list[(priority, hook_instance, hook_decl)] for loaded hook impls
        self._hook_instances: dict[str, list[tuple[int, ExtensionHook, HookDecl]]] = {}

    # ── path resolution ───────────────────────────────────────────────────
    def _corvin_home(self) -> Path:
        try:
            from .paths import corvin_home  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from paths import corvin_home  # type: ignore
        return corvin_home()

    def _tenant_home(self) -> Path:
        try:
            from .paths import tenant_home  # type: ignore
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from paths import tenant_home  # type: ignore
        return tenant_home(self._tenant_id)

    def _project_extensions_dir(self) -> Path:
        base = self._project_root if self._project_root is not None else Path.cwd()
        return Path(base) / ".corvin" / "extensions"

    def scope_dir(self, scope: str) -> Path | None:
        """Return the storage directory for a scope, or None for ``task``
        (in-memory only)."""
        if scope == "tenant":
            return self._tenant_home() / "extensions"
        if scope == "project":
            return self._project_extensions_dir()
        if scope == "user":
            return self._corvin_home() / "global" / "extensions"
        if scope == "session":
            if not self._session_id:
                return None
            return self._tenant_home() / "sessions" / self._session_id / "extensions"
        if scope == "task":
            return None  # in-memory only, not persisted
        raise ExtensionError(f"unknown scope {scope!r}")

    # ── loading ───────────────────────────────────────────────────────────
    def _audit(self, event: str, *, name: str = "", version: str = "",
               scope: str = "", hook: str = "", reason: str = "") -> None:
        """Best-effort ext.* audit emit (allow-listed fields only)."""
        details: dict[str, Any] = {}
        if name:
            details["name"] = name
        if version:
            details["version"] = version
        if scope:
            details["scope"] = scope
        if hook:
            details["hook"] = hook
        if reason:
            details["reason"] = reason
        ctx = HookContext(tenant_id=self._tenant_id or "_default", ext_name=name,
                          ext_version=version, ext_scope=scope)
        ctx.audit_write(event, details)

    def load_manifest_file(self, manifest_path: Path) -> ExtensionManifest:
        """Read + validate a ``layer.yaml`` file, run the namespace gate and the
        requires check. Emits ext.core_namespace_rejected on a namespace
        violation and ext.load_failed on any other load failure. Raises on
        failure (fail-to-load contract)."""
        import yaml
        try:
            raw = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
        except Exception as exc:
            self._audit("ext.load_failed", reason=f"yaml-parse-error")
            raise ExtensionManifestError(f"cannot parse {manifest_path}: {exc}") from exc

        # Parse the schema first so we know name/version/scope for audit fields,
        # but run the namespace gate before accepting it into the registry.
        try:
            manifest = parse_manifest(raw, root=Path(manifest_path).parent)
        except ExtensionManifestError:
            nm = ""
            if isinstance(raw, dict):
                nm = str(raw.get("name", ""))
            self._audit("ext.load_failed", name=nm, reason="manifest-invalid")
            raise

        # Namespace gate — CRITICAL. Covers manifest.name AND every provides[].name:
        # an extension must not be able to claim a core corvin.* capability via
        # `provides` (which feeds capability resolution once M7 wires it).
        try:
            validate_name(manifest.name)
            for _prov in manifest.provides:
                validate_name(str(_prov.get("name", "")))
        except ExtensionNamespaceError as exc:
            self._audit(
                "ext.core_namespace_rejected",
                name=manifest.name, version=manifest.version,
                scope=manifest.scope, reason="core-namespace",
            )
            raise

        # Core dependency check — fail-to-load if unmet.
        try:
            check_requires(manifest, self.core_capabilities)
        except ExtensionDependencyError as exc:
            self._audit(
                "ext.load_failed",
                name=manifest.name, version=manifest.version,
                scope=manifest.scope, reason="missing-requires",
            )
            raise

        return manifest

    def _enabled_state(self, ext_dir: Path) -> bool:
        """Read the ``.enabled`` marker for an installed extension. Default-off:
        absent marker => disabled (production safety)."""
        return (ext_dir / ".enabled").exists()

    def discover(self) -> dict[str, ExtensionManifest]:
        """Scan all persisted scopes in resolution order and load every valid
        manifest. Most-specific scope wins for a duplicate name (provides
        semantics). Invalid/failed manifests are skipped (already audited).
        Returns the loaded manifest map and populates self._extensions."""
        self._extensions.clear()
        self._hook_instances.clear()
        for scope in SCOPES:
            d = self.scope_dir(scope)
            if d is None or not d.is_dir():
                continue
            for ext_dir in sorted(d.iterdir()):
                if not ext_dir.is_dir():
                    continue
                manifest_path = ext_dir / "layer.yaml"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = self.load_manifest_file(manifest_path)
                except ExtensionError:
                    continue  # skip; load failure already audited
                if manifest.name in self._extensions:
                    continue  # most-specific scope already won
                manifest.enabled = self._enabled_state(ext_dir)
                manifest.root = ext_dir
                self._extensions[manifest.name] = manifest
        return self._extensions

    # ── hook instantiation ──────────────────────────────────────────────────
    def _load_hook_instances(self, event: str) -> list[tuple[int, str, ExtensionHook, HookDecl, ExtensionManifest]]:
        """Instantiate ExtensionHook subclasses for all enabled extensions that
        declare a hook for ``event``. Returns a list of
        ``(priority, name, hook, decl, manifest)``. Load failures are audited
        (ext.load_failed) and skipped."""
        import importlib.util
        out: list[tuple[int, str, ExtensionHook, HookDecl, ExtensionManifest]] = []
        for name, manifest in self._extensions.items():
            if not manifest.enabled or manifest.root is None:
                continue
            for decl in manifest.hooks:
                if decl.event != event:
                    continue
                script_path = (manifest.root / decl.script).resolve()
                try:
                    # Real path containment, not a string prefix: startswith()
                    # admits sibling-prefix escapes (/ext/foo accepts /ext/foobar).
                    if not script_path.is_relative_to(manifest.root.resolve()):
                        raise ExtensionError("hook script escapes extension root")
                    if script_path.is_symlink():
                        raise ExtensionError("hook script must not be a symlink")
                    spec = importlib.util.spec_from_file_location(
                        f"corvin_ext_{name.replace('.', '_')}_{decl.event}", script_path
                    )
                    if spec is None or spec.loader is None:
                        raise ExtensionError("cannot load hook module spec")
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    hook_cls = None
                    for attr in vars(mod).values():
                        if (isinstance(attr, type) and issubclass(attr, ExtensionHook)
                                and attr is not ExtensionHook):
                            hook_cls = attr
                            break
                    if hook_cls is None:
                        raise ExtensionError("no ExtensionHook subclass found in script")
                    instance = hook_cls()
                    priority = decl.priority if decl.priority else getattr(instance, "priority", 0)
                    out.append((int(priority), name, instance, decl, manifest))
                except Exception:
                    self._audit("ext.load_failed", name=name, version=manifest.version,
                                scope=manifest.scope, hook=decl.event, reason="hook-load-error")
                    continue
        return out

    # ── M3 — hook execution pipeline ───────────────────────────────────────
    def run_pre_tool_use(
        self, tool_name: str, tool_input: dict, ctx: HookContext,
    ) -> HookResult:
        """Run the PreToolUse pipeline with deny-wins semantics.

        Order: core hooks first (priority 0, registration order), then extension
        hooks sorted by priority desc then name asc. ALL hooks run; the final
        verdict is deny if ANY hook denied. An extension can never un-deny a
        core deny. An extension deny emits ext.hook_denied.
        """
        final_deny = False
        first_reason = ""

        # 1. Core hooks (priority 0, fixed registration order).
        for _prio, _name, hook in self._core_hooks:
            try:
                res = hook.handle(tool_name, tool_input, ctx)
            except Exception:
                # A core hook that raises is treated as a deny (fail-closed) —
                # core is the security baseline.
                res = HookResult.deny("core-hook-error")
            if res is not None and res.is_deny:
                final_deny = True
                if not first_reason:
                    first_reason = res.reason

        # 2. Extension hooks (priority desc, then name asc).
        ext_hooks = self._load_hook_instances("PreToolUse")
        ext_hooks.sort(key=lambda e: (-e[0], e[1]))
        for _prio, name, hook, decl, manifest in ext_hooks:
            ext_ctx = HookContext(
                tenant_id=ctx.tenant_id, session_id=ctx.session_id,
                channel=ctx.channel, chat_key=ctx.chat_key, persona=ctx.persona,
                config=ctx.config, ext_name=name, ext_version=manifest.version,
                ext_scope=manifest.scope,
                audit_writer=ctx._audit_writer, audit_path_fn=ctx._audit_path_fn,
            )
            try:
                res = hook.handle(tool_name, tool_input, ext_ctx)
            except Exception:
                # An extension hook that raises does NOT block (extensions only
                # add restrictions; a buggy extension must not break the action),
                # but it is audited as a load/run failure.
                self._audit("ext.load_failed", name=name, version=manifest.version,
                            scope=manifest.scope, hook="PreToolUse", reason="hook-run-error")
                continue
            if res is not None and res.is_deny:
                final_deny = True
                if not first_reason:
                    first_reason = res.reason
                # Emit ext.hook_denied (allow-listed fields only).
                self._audit(
                    "ext.hook_denied", name=name, version=manifest.version,
                    scope=manifest.scope, hook="PreToolUse", reason=res.reason,
                )

        return HookResult.deny(first_reason or "denied") if final_deny else HookResult.allow()

    # ── M4 helpers (CLI surface uses these) ────────────────────────────────
    def list_core(self) -> list[dict[str, Any]]:
        """Return the immutable core-layer list for `corvin-layer list`."""
        out: list[dict[str, Any]] = []
        for name, version in self.core_capabilities.items():
            out.append({
                "name": name,
                "version": version,
                "active": True,
                "core": True,
                "description": CORE_LAYER_DESC.get(name, ""),
            })
        return out

    def list_extensions(self) -> list[ExtensionManifest]:
        if not self._extensions:
            self.discover()
        return list(self._extensions.values())

    def get(self, name: str) -> ExtensionManifest | None:
        if not self._extensions:
            self.discover()
        return self._extensions.get(name)
