"""ldd.py — toggle library for the LDD layer suite (Layer 14).

Companion to ``dialectic.py`` (Layer 11) and ``skill_inject.py`` (skill
availability). Where dialectic.py owns *decision sites*, this module
owns *which LDD disciplines are active at all*. A layer that is OFF
gets filtered out of skill-injection AND silenced at every native
integration point that consults ``is_layer_active(...)``.

Storage
-------
``<scope_root>/global/ldd.json``, mtime-cached. Same pattern as
``dialectic.json``. Default-on. Per-chat override via
``profile.ldd_enabled = False`` (master) or
``profile.ldd_layers = {<layer>: bool}`` (per-layer).

Layer-Set (canonical IDs use underscores, never hyphens or colons)
------------------------------------------------------------------
- loop_driven_engineering
- e2e_driven_iteration
- dialectical_reasoning      (master-couples Layer 11)
- dialectical_cot
- root_cause_by_layer
- docs_as_dod                (alias: docs_as_definition_of_done)
- reproducibility_first
- loss_backprop_lens
- method_evolution
- drift_detection
- iterative_refinement
- per_subtask_e2e            (project-specific rule from CLAUDE.md)

``using_ldd`` is the bootstrap entry-point and intentionally NOT in the
toggle set — disabling it would break every other layer's dispatch.

Cost contract
-------------
This module MUST NOT import the Anthropic SDK (mirror of dialectic.py's
contract). All decisions here are local file-IO + dictionary lookups,
sub-millisecond. A repo-level CI lint rejects ``import anthropic`` in
this file; do not add it.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable


# ── Canonical layer set ────────────────────────────────────────────────────

LAYERS: tuple[str, ...] = (
    "loop_driven_engineering",
    "e2e_driven_iteration",
    "dialectical_reasoning",
    "dialectical_cot",
    "root_cause_by_layer",
    "docs_as_dod",
    "reproducibility_first",
    "loss_backprop_lens",
    "method_evolution",
    "drift_detection",
    "iterative_refinement",
    "per_subtask_e2e",
)

# Hard cascade: layer X cannot be active without layer Y. Mapping is
# child → parent; only THREE bewiesen-sinnlose pairs are wired here, and
# the resolver is recursive so future multi-level cascades work without
# code change.
#
# Why these three:
#   - dialectical_cot is SGD-on-thoughts WITH dialect steps — without
#     dialectical_reasoning the step-mechanism is undefined.
#   - per_subtask_e2e is a hardening rule on top of the E2E loop;
#     without e2e_driven_iteration there is no loop to harden.
#   - drift_detection scans docs as the source-of-truth — without
#     docs_as_dod the doc base is not reliable enough for drift signal.
#
# Add a new entry only when (1) the child is conceptually a refinement
# of the parent AND (2) running the child without the parent produces
# silent no-op work (not just suboptimal work). For "soft" couplings
# the operator gets a warning at /ldd-set time but no cascade.
DEPENDS_ON: dict[str, str] = {
    "dialectical_cot":  "dialectical_reasoning",
    "per_subtask_e2e":  "e2e_driven_iteration",
    "drift_detection":  "docs_as_dod",
}

# Aliases — the canonical skill name (after lower-case + dash→underscore +
# plugin-prefix strip) to the layer ID. Multiple skill-names may collapse
# onto one layer (e.g. dialectic-reasoning v1 vs. v2 wording).
_NAME_ALIASES: dict[str, str] = {
    "docs_as_definition_of_done": "docs_as_dod",
}

# Presets — group toggles by intent. Operator picks one with /ldd-preset.
PRESETS: dict[str, dict[str, bool]] = {
    "default": {layer: True for layer in LAYERS},
    "strict":  {layer: True for layer in LAYERS},
    "quick": {
        # Keep the bare-minimum gates that catch shipped regressions:
        # E2E + docs + per-subtask E2E + dialectic on the recommendation
        # surface. Everything else off (skip-the-discipline mode for fast
        # exploratory work).
        "loop_driven_engineering": False,
        "e2e_driven_iteration":    True,
        "dialectical_reasoning":   True,
        "dialectical_cot":         False,
        "root_cause_by_layer":     False,
        "docs_as_dod":             True,
        "reproducibility_first":   False,
        "loss_backprop_lens":      False,
        "method_evolution":        False,
        "drift_detection":         False,
        "iterative_refinement":    False,
        "per_subtask_e2e":         True,
    },
    "off": {layer: False for layer in LAYERS},
}


# ── Skill-name → layer mapping ─────────────────────────────────────────────

def normalize_skill_name(name: str) -> str:
    """Canonicalize a skill name to a layer-id candidate.

    Steps: strip plugin-prefix (``foo:bar`` → ``bar``), lower-case,
    replace ``-`` with ``_``. Result is then looked up in ``_NAME_ALIASES``
    so the historical ``docs-as-definition-of-done`` wording resolves
    onto the canonical ``docs_as_dod`` layer.
    """
    if not name:
        return ""
    n = str(name)
    if ":" in n:
        n = n.split(":", 1)[1]
    n = n.lower().replace("-", "_")
    return _NAME_ALIASES.get(n, n)


def layer_for_skill_name(name: str) -> str | None:
    """Return the layer ID this skill-name maps to, or None when the
    skill is not part of the LDD-toggle set (e.g. domain skills, custom
    forge skills, the ``using-ldd`` bootstrap)."""
    candidate = normalize_skill_name(name)
    return candidate if candidate in LAYERS else None


# ── Configuration with mtime-based hot reload ──────────────────────────────

_CONFIG_LOCK = threading.RLock()
_CONFIG_CACHE: dict[str, Any] = {}
_CONFIG_MTIME: float = 0.0


def _config_path(*, tenant_id: str | None = None) -> Path:
    """Resolve <scope_root>/global/ldd.json. Falls back to a per-user
    location when forge.paths is unimportable (mirrors dialectic.py).

    ADR-0007 Phase 1.3: optional tenant_id kwarg places the config under
    <scope_root>/tenants/<tid>/global/ldd.json. Default None preserves
    the legacy single-operator path.
    """
    middle = ("tenants", tenant_id, "global") if tenant_id else ("global",)
    try:
        from forge.paths import corvin_home  # type: ignore  # noqa: PLC0415
        return Path(corvin_home()).joinpath(*middle, "ldd.json")
    except Exception:  # noqa: BLE001
        env = os.environ.get("CORVIN_HOME")
        if env:
            return Path(env).joinpath(*middle, "ldd.json")
        return Path.home().joinpath(".corvin", *middle, "ldd.json")


def _default_config() -> dict[str, Any]:
    # Default-OFF since the policy switch: LDD soft-discipline does not
    # ship enabled by default. The hard structural enforcement (forge
    # sandbox, skill-forge linter, path-gate, audit hash-chain, promotion
    # gates) lives outside this toggle system and stays active regardless.
    return {
        "enabled": False,
        "layers": {layer: False for layer in LAYERS},
    }


def load_config() -> dict[str, Any]:
    """Hot-reload aware config getter. Writes the default file on first
    call if missing (best-effort — tolerates read-only FS)."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    p = _config_path()
    with _CONFIG_LOCK:
        if not p.exists():
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                cfg = _default_config()
                p.write_text(json.dumps(cfg, indent=2))
                _CONFIG_CACHE = cfg
                _CONFIG_MTIME = p.stat().st_mtime
                return dict(cfg)
            except OSError:
                return _default_config()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return dict(_CONFIG_CACHE) if _CONFIG_CACHE else _default_config()
        if not _CONFIG_CACHE or mtime != _CONFIG_MTIME:
            try:
                raw = json.loads(p.read_text())
                if not isinstance(raw, dict):
                    raise json.JSONDecodeError("not an object", "", 0)
                # Shallow-merge over defaults so a missing key never
                # crashes downstream.
                merged = _default_config()
                if isinstance(raw.get("enabled"), bool):
                    merged["enabled"] = raw["enabled"]
                layers = raw.get("layers")
                if isinstance(layers, dict):
                    for k, v in layers.items():
                        if k in LAYERS and isinstance(v, bool):
                            merged["layers"][k] = v
                _CONFIG_CACHE = merged
                _CONFIG_MTIME = mtime
            except (OSError, json.JSONDecodeError):
                # Corrupt config — return defaults but DON'T overwrite
                # the on-disk file (the operator may want to fix it).
                _CONFIG_CACHE = _default_config()
        return dict(_CONFIG_CACHE)


def save_config(cfg: dict[str, Any]) -> None:
    """Atomic write + cache bust."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with _CONFIG_LOCK:
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(p)
        _CONFIG_CACHE = dict(cfg)
        _CONFIG_MTIME = p.stat().st_mtime


# ── Public API ─────────────────────────────────────────────────────────────

def master_enabled(*, profile: dict | None = None) -> bool:
    """True iff the LDD master switch is on.

    Priority (highest first):
      1. profile.ldd_enabled = False  → always off (explicit persona disable)
      2. LDD_AUTO_OPTIN=1 env var     → always on (operator opt-in)
      3. cfg.enabled from ldd.json    → file-based toggle
    """
    profile = profile or {}
    if profile.get("ldd_enabled") is False:
        return False
    if os.environ.get("LDD_AUTO_OPTIN") == "1":
        return True
    cfg = load_config()
    return bool(cfg.get("enabled", False))


def _direct_state(layer: str, *, profile: dict, cfg: dict) -> bool:
    """Compute the layer's state ignoring any cascade.

    Precedence (highest first):
      profile per-layer > profile master > LDD_AUTO_OPTIN env var >
      cfg master > cfg per-layer > default-on.
    Cascade-evaluation is done by the caller.
    """
    pl = profile.get("ldd_layers")
    if isinstance(pl, dict) and layer in pl and isinstance(pl[layer], bool):
        return bool(pl[layer])
    if profile.get("ldd_enabled") is False:
        return False
    # LDD_AUTO_OPTIN=1 activates every layer that has no explicit per-layer
    # profile-disable above. Cascade checks in is_layer_active() still apply.
    if os.environ.get("LDD_AUTO_OPTIN") == "1":
        return True
    if not cfg.get("enabled", False):
        return False
    layers = cfg.get("layers", {})
    if layer in layers and isinstance(layers[layer], bool):
        return bool(layers[layer])
    # Master is on but layer has no explicit entry: default-on (preserves
    # the historical fail-open behaviour for typos / new layers).
    return True


def is_layer_active(layer: str, *, profile: dict | None = None) -> bool:
    """True iff ``layer`` is currently enabled, taking cascades into account.

    Resolution order:
      1. Direct state via ``_direct_state``: profile per-layer > profile
         master > cfg master > cfg per-layer > default-on.
      2. **Hard cascade**: if the layer has a parent in ``DEPENDS_ON``
         and that parent is inactive, this layer is inactive too —
         even an explicit ``profile.ldd_layers[child] = True`` cannot
         override the cascade. Operators must lift the parent (per
         profile or globally) to reactivate the child.

    Unknown layer IDs always return True (fail-open).
    """
    if layer not in LAYERS:
        return True
    profile = profile or {}
    cfg = load_config()
    if not _direct_state(layer, profile=profile, cfg=cfg):
        return False
    parent = DEPENDS_ON.get(layer)
    if parent is not None and not is_layer_active(parent, profile=profile):
        return False
    return True


def effective_state(layer: str, *, profile: dict | None = None) -> tuple[bool, str]:
    """Return ``(active, reason)`` for ``layer``.

    ``reason`` is one of:
      - ``"on"``                 — directly on, no cascade involved
      - ``"manual_off"``         — directly off via cfg or profile per-layer
      - ``"profile_master_off"`` — profile.ldd_enabled = False
      - ``"global_master_off"``  — cfg.enabled = False
      - ``"cascade_off:<parent>"`` — direct state on, but parent is off

    Useful for ``/ldd-status`` output and ``/ldd-set`` warnings. Unknown
    layer IDs return ``(True, "on")``.
    """
    if layer not in LAYERS:
        return True, "on"
    profile = profile or {}
    cfg = load_config()
    # Master kill diagnostics — explicit per-layer overrides still beat
    # the master, so we have to consult _direct_state below first.
    direct = _direct_state(layer, profile=profile, cfg=cfg)
    if not direct:
        # Pin down the *most-specific* reason for the off-state.
        pl = profile.get("ldd_layers") or {}
        if layer in pl and pl[layer] is False:
            return False, "manual_off"
        if profile.get("ldd_enabled") is False:
            return False, "profile_master_off"
        if not cfg.get("enabled", True):
            return False, "global_master_off"
        return False, "manual_off"
    parent = DEPENDS_ON.get(layer)
    if parent is not None and not is_layer_active(parent, profile=profile):
        return False, f"cascade_off:{parent}"
    return True, "on"


def set_layer(layer: str, on: bool) -> None:
    """Persist a per-layer toggle."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer: {layer!r}")
    cfg = load_config()
    cfg.setdefault("layers", {})[layer] = bool(on)
    save_config(cfg)


def set_master(on: bool) -> None:
    cfg = load_config()
    cfg["enabled"] = bool(on)
    save_config(cfg)


def apply_preset(name: str) -> dict[str, bool]:
    """Apply one of the named presets. Returns the resulting layers dict."""
    if name not in PRESETS:
        raise ValueError(
            f"unknown preset: {name!r} — valid: {sorted(PRESETS.keys())}")
    cfg = load_config()
    cfg["enabled"] = True if name != "off" else False
    cfg["layers"] = dict(PRESETS[name])
    save_config(cfg)
    return dict(cfg["layers"])


def filter_skills(specs: Iterable, *, profile: dict | None = None) -> list:
    """Filter an iterable of skill-spec objects by LDD layer activity.

    A spec is dropped iff its name maps to a known LDD layer AND that
    layer is currently inactive. Specs whose names don't match any LDD
    layer (the typical "domain skill") pass through untouched.

    Caller passes anything with a ``.name`` attribute; the function does
    not depend on skill-forge package internals.
    """
    out = []
    for spec in specs:
        name = getattr(spec, "name", "") or ""
        layer = layer_for_skill_name(name)
        if layer is not None and not is_layer_active(layer, profile=profile):
            continue
        out.append(spec)
    return out


# ── CLI entrypoint (used by /ldd-* slash-commands) ─────────────────────────

def _cli_status() -> int:
    cfg = load_config()
    enabled = cfg.get("enabled", True)
    print(f"ldd: enabled={enabled}")
    print("layers:")
    for layer in LAYERS:
        active, reason = effective_state(layer)
        flag = "on " if active else "OFF"
        suffix = ""
        if reason.startswith("cascade_off:"):
            suffix = f"  (cascade: parent {reason.split(':', 1)[1]} off)"
        elif active and layer in DEPENDS_ON:
            suffix = f"  (depends on {DEPENDS_ON[layer]})"
        print(f"  [{flag}] {layer}{suffix}")
    if DEPENDS_ON:
        print("\ndependencies (child -> parent):")
        for child, parent in DEPENDS_ON.items():
            print(f"  {child} -> {parent}")
    print(f"\nconfig: {_config_path()}")
    print(f"presets: {', '.join(sorted(PRESETS.keys()))}")
    return 0


def _cli_on() -> int:
    set_master(True)
    print("ldd: ENABLED globally — per-layer toggles take effect")
    return 0


def _cli_off() -> int:
    set_master(False)
    print("ldd: DISABLED globally — every layer is now off")
    return 0


def _cli_set(layer: str, mode: str) -> int:
    layer = layer.strip().lower()
    if layer not in LAYERS:
        # Be helpful: try to canonicalize a hyphenated form.
        canon = normalize_skill_name(layer)
        if canon in LAYERS:
            layer = canon
        else:
            print(f"unknown layer: {layer!r}")
            print(f"valid: {list(LAYERS)}")
            return 1
    on_words = {"on", "true", "1", "yes", "enable"}
    off_words = {"off", "false", "0", "no", "disable"}
    m = mode.strip().lower()
    if m in on_words:
        set_layer(layer, True)
        print(f"ldd: {layer} -> on")
        # Warn when this layer can't actually take effect because its
        # parent is currently off (cascade gate). The set is still
        # persisted — the moment the parent flips back on, the child
        # auto-activates.
        parent = DEPENDS_ON.get(layer)
        if parent is not None and not is_layer_active(parent):
            print(
                f"⚠ depends on {parent} (currently OFF) — {layer} stays "
                f"inactive until you /ldd-set {parent} on"
            )
        return 0
    if m in off_words:
        set_layer(layer, False)
        print(f"ldd: {layer} -> off")
        # Inform about children that just lost their parent.
        children = [c for c, p in DEPENDS_ON.items() if p == layer]
        if children:
            print(
                f"↳ cascade: {', '.join(children)} now inactive too "
                f"(depends on {layer})"
            )
        return 0
    print(f"unknown mode: {mode!r} — use on/off")
    return 1


def _cli_preset(name: str) -> int:
    n = name.strip().lower()
    if n not in PRESETS:
        print(f"unknown preset: {name!r}")
        print(f"valid: {sorted(PRESETS.keys())}")
        return 1
    apply_preset(n)
    print(f"ldd: preset '{n}' applied")
    return _cli_status()


def _cli_main(argv: list[str]) -> int:
    if not argv or argv[0] in ("status", "-h", "--help", "help"):
        return _cli_status()
    sub = argv[0].lower()
    if sub == "on":
        return _cli_on()
    if sub == "off":
        return _cli_off()
    if sub == "set":
        if len(argv) < 3:
            print("usage: ldd.py set <layer> <on|off>")
            return 1
        return _cli_set(argv[1], argv[2])
    if sub == "preset":
        if len(argv) < 2:
            print(f"usage: ldd.py preset <{'|'.join(sorted(PRESETS.keys()))}>")
            return 1
        return _cli_preset(argv[1])
    print(f"unknown command: {sub!r}")
    return _cli_status()


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
