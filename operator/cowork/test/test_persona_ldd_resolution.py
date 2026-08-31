#!/usr/bin/env python3
"""E2E tests for per-persona LDD profile resolution (Layer 14).

Default-OFF policy: every bundle persona ships with ``ldd_preset: "off"``
so the soft LDD discipline does NOT inject by default. The hard
structural enforcement (forge sandbox, skill-forge linter, path-gate,
audit hash-chain, promotion gates) lives outside the LDD toggle and
stays active regardless.

This suite asserts:
  - Every persona resolves to "all layers off" with no chat override
  - chat_profile.ldd_layers per-layer override beats persona preset
  - chat_profile.ldd_enabled master flag lifts / kills the persona
  - cascade (dialectical_cot → dialectical_reasoning etc.) still
    applies on top of the resolved profile
  - Schema-smoke: every bundle persona JSON parses + has valid LDD
    fields (preset, layers, enabled types)

Run: python3 operator/cowork/test/test_persona_ldd_resolution.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "cowork" / "lib"))
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox CORVIN_HOME so writes of ldd.json don't poison the real
# workspace.
_TD = Path(tempfile.mkdtemp(prefix="persona-ldd-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
# Don't let the shell's global LDD_AUTO_OPTIN=1 (~/.bashrc) override the
# file-based toggles under test — mirrors test_ldd_lib.py.
os.environ.pop("LDD_AUTO_OPTIN", None)

import resolver  # noqa: E402  cowork
import ldd       # noqa: E402  voice


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_ldd():
    p = ldd._config_path()
    if p.exists():
        p.unlink()
    ldd._CONFIG_CACHE = {}
    ldd._CONFIG_MTIME = 0.0


# ── Spec table — single source of truth ───────────────────────────────────


# Every bundle persona ships with ldd_preset: "off". The hard structural
# enforcement (forge sandbox, skill-forge linter, path-gate, audit
# hash-chain, promotion gates) lives outside the LDD toggle, so "all
# layers off" here does NOT disable the load-bearing safety surface.
_OFF = {layer: False for layer in ldd.LAYERS}

EXPECTED: dict[str, dict[str, bool]] = {
    "coder":         dict(_OFF),
    "forge":         dict(_OFF),
    "browser":       dict(_OFF),
    "research":      dict(_OFF),
    "inbox":         dict(_OFF),
    "homeassistant": dict(_OFF),
    "assistant":     dict(_OFF),
    "os":            dict(_OFF),
}


# ── 1. Spec-table coherence under cascade ──────────────────────────────────


def case_spec_cascade_consistent():
    print("\n[1] Spec table is itself cascade-consistent")
    for persona, layers in EXPECTED.items():
        for child, parent in ldd.DEPENDS_ON.items():
            if layers.get(child) and not layers.get(parent):
                t(f"{persona}: {child} on must imply {parent} on", False,
                  detail=f"{child}={layers.get(child)} {parent}={layers.get(parent)}")
                return
    t("spec table is cascade-consistent for every persona", True)


# ── 2. Per-persona × per-layer (8 × 12 = 96 assertions) ────────────────────


def case_persona_baseline():
    for persona_name, layer_states in EXPECTED.items():
        print(f"\n[2.{persona_name}] effective layer states (no chat overrides)")
        reset_ldd()
        profile = resolver.resolve(persona_name, overrides={})
        for layer, expected in layer_states.items():
            got = ldd.is_layer_active(layer, profile=profile)
            t(f"{persona_name}/{layer} → {'on' if expected else 'off'}",
              got == expected,
              detail=(f"got={got}, profile.ldd_enabled={profile.get('ldd_enabled')!r}, "
                      f"profile.ldd_layers[{layer}]={profile.get('ldd_layers', {}).get(layer)!r}")
                     if got != expected else "")


# ── 3. Override stack: chat_profile beats persona ──────────────────────────


def case_chat_profile_per_layer_overrides_persona():
    print("\n[3] chat_profile.ldd_layers per-layer beats persona preset")
    reset_ldd()
    # browser: preset off → every layer off. Chat override flips two on,
    # leaves a third untouched.
    overrides = {"ldd_layers": {"e2e_driven_iteration": True,
                                "root_cause_by_layer": True}}
    profile = resolver.resolve("browser", overrides=overrides)
    t("browser + override e2e=True → on (beats master kill)",
      ldd.is_layer_active("e2e_driven_iteration", profile=profile) is True)
    t("browser + override root_cause=True → on",
      ldd.is_layer_active("root_cause_by_layer", profile=profile) is True)
    # Untouched layer stays off (preset-derived False).
    t("browser + override leaves unrelated layer (docs_as_dod) off",
      ldd.is_layer_active("docs_as_dod", profile=profile) is False)


def case_chat_profile_master_overrides_persona():
    print("\n[4] chat_profile.ldd_enabled beats persona master")
    reset_ldd()
    # inbox: ldd_preset=off → master False from persona. Chat master off
    # is redundant but exercises the override path; every layer remains off.
    profile = resolver.resolve("inbox", overrides={"ldd_enabled": False})
    t("inbox + chat-master-off → dialectical_reasoning off",
      ldd.is_layer_active("dialectical_reasoning", profile=profile) is False)
    # homeassistant: master-off from persona; chat lifts master to True
    # WITHOUT touching layer values (preset still wired all-off).
    profile2 = resolver.resolve("homeassistant", overrides={"ldd_enabled": True})
    for layer in ldd.LAYERS:
        if ldd.is_layer_active(layer, profile=profile2):
            t(f"ha + master-on still has {layer} off (preset off keeps layers off)",
              False)
            return
    t("ha + master-on still has every layer off (preset wins on layer values)",
      True)


def case_chat_profile_combination():
    print("\n[5] chat_profile combines per-layer + master override")
    reset_ldd()
    # ha: preset off, master off, every layer off.
    # chat_profile: enable master + flip dialectical_reasoning on.
    overrides = {
        "ldd_enabled": True,
        "ldd_layers": {"dialectical_reasoning": True},
    }
    profile = resolver.resolve("homeassistant", overrides=overrides)
    t("ha + chat overrides (master+layer) → dialectical_reasoning on",
      ldd.is_layer_active("dialectical_reasoning", profile=profile) is True)
    t("ha + chat overrides → other layers still off",
      ldd.is_layer_active("e2e_driven_iteration", profile=profile) is False)


# ── 4. Cascade applies on top of persona profile ───────────────────────────


def case_cascade_applies_to_persona_profile():
    print("\n[6] Cascade still applies after persona resolution")
    reset_ldd()
    # browser preset=off → every layer off. Chat override turns BOTH the
    # cot child and the dialectical_reasoning parent on → cascade is
    # satisfied → child resolves to on.
    profile = resolver.resolve(
        "browser",
        overrides={"ldd_layers": {"dialectical_cot": True,
                                   "dialectical_reasoning": True}},
    )
    t("browser + chat-cot=True + parent=True → on (cascade satisfied)",
      ldd.is_layer_active("dialectical_cot", profile=profile) is True)

    # Flip parent off via chat → cascade kicks in even though child=True.
    profile2 = resolver.resolve(
        "browser",
        overrides={"ldd_layers": {"dialectical_cot": True,
                                   "dialectical_reasoning": False}},
    )
    t("browser + parent-off + chat-cot=True → off (cascade)",
      ldd.is_layer_active("dialectical_cot", profile=profile2) is False)


def case_cascade_with_master_off():
    print("\n[7] Cascade interacts correctly with chat-level master kill")
    reset_ldd()
    # coder preset=off (since the default-off policy); chat tries to opt
    # IN per-layer but flips master off — the chat-master-off rule drops
    # persona-injected layer entries, so every layer stays off.
    profile = resolver.resolve(
        "coder",
        overrides={
            "ldd_enabled": False,
            "ldd_layers": {},  # no chat-explicit overrides
        },
    )
    for layer in ldd.LAYERS:
        if ldd.is_layer_active(layer, profile=profile):
            t(f"coder + chat-master-off → {layer} should be off",
              False)
            return
    t("coder + chat-master-off → every layer off", True)


# ── 5. Schema smoke — every bundle persona JSON is valid ──────────────────


def case_schema_smoke():
    print("\n[8] Bundle persona JSONs have valid LDD fields")
    bundle = REPO / "operator" / "cowork" / "personas"
    valid_presets = set(ldd.PRESETS.keys())
    for path in sorted(bundle.glob("*.json")):
        name = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            t(f"{name}.json parses", False, detail=str(e))
            continue
        t(f"{name}.json parses + is dict", isinstance(data, dict))
        preset = data.get("ldd_preset")
        if preset is not None:
            t(f"{name}.json: ldd_preset={preset!r} is valid",
              preset in valid_presets,
              detail=f"valid={sorted(valid_presets)}")
        layers = data.get("ldd_layers")
        if layers is not None:
            t(f"{name}.json: ldd_layers is a dict",
              isinstance(layers, dict))
            if isinstance(layers, dict):
                for k, v in layers.items():
                    t(f"{name}.json: ldd_layers[{k}] is bool",
                      isinstance(v, bool))
                    t(f"{name}.json: ldd_layers[{k}] references known layer",
                      k in ldd.LAYERS,
                      detail=f"unknown: {k}")
        enabled = data.get("ldd_enabled")
        if enabled is not None:
            t(f"{name}.json: ldd_enabled is bool",
              isinstance(enabled, bool))


# ── 6. Resolver passes raw fields through when ldd is unavailable ─────────


def case_resolver_handles_missing_ldd_module():
    print("\n[9] Resolver tolerates ldd module absence (cached lookup)")
    # We can't actually uninstall ldd here, but we can prove the
    # raw-field passthrough works when no preset is set.
    reset_ldd()
    # Synthetic persona dict with only ldd_layers (no preset)
    out = resolver._resolve_ldd_section(
        persona={"ldd_layers": {"e2e_driven_iteration": False}},
        overrides={},
    )
    t("ldd_layers without preset still flows through",
      out.get("ldd_layers") == {"e2e_driven_iteration": False})


def main():
    case_spec_cascade_consistent()
    case_persona_baseline()
    case_chat_profile_per_layer_overrides_persona()
    case_chat_profile_master_overrides_persona()
    case_chat_profile_combination()
    case_cascade_applies_to_persona_profile()
    case_cascade_with_master_off()
    case_schema_smoke()
    case_resolver_handles_missing_ldd_module()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
