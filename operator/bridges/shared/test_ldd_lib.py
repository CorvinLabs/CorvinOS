#!/usr/bin/env python3
"""E2E tests for the LDD-toggle library (Layer 14).

Covers:
  - Layer set is the canonical 12 disciplines (no `using_ldd` bootstrap)
  - load_config writes a default file on first call (mtime-cached)
  - save_config + hot-reload via mtime change
  - master switch: cfg.enabled = False forces every layer off
  - per-layer toggle persists round-trip
  - profile.ldd_enabled = False is a per-chat master kill
  - profile.ldd_layers[layer] is a per-chat per-layer override
  - explicit per-layer override beats master-disable per chat
  - presets: default / strict / quick / off — each lands the expected
    layer-state on disk
  - skill-name → layer mapping handles plugin-prefix, hyphen, alias
  - filter_skills drops LDD skills whose layer is off, keeps others
  - CLI: status / on / off / set / preset round-trip
  - CI-lint: ``import anthropic`` is forbidden

Run as: python3 operator/bridges/shared/test_ldd_lib.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox CORVIN_HOME BEFORE importing ldd so config writes go to /tmp.
_TD = Path(tempfile.mkdtemp(prefix="ldd-lib-test-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
# Suppress LDD_AUTO_OPTIN during the main test cases so that file-based
# toggle tests can work with cfg.enabled=False without the env var overriding.
_AUTO_OPTIN_SAVED = os.environ.pop("LDD_AUTO_OPTIN", None)

import ldd  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_config():
    """Wipe on-disk config + cache so each block starts fresh."""
    p = ldd._config_path()
    if p.exists():
        p.unlink()
    ldd._CONFIG_CACHE = {}
    ldd._CONFIG_MTIME = 0.0


# ── 1. Canonical layer set ─────────────────────────────────────────────────

def case_layer_set():
    print("\n[1] Layer-set is the 12 canonical LDD disciplines")
    expected = {
        "loop_driven_engineering", "e2e_driven_iteration",
        "dialectical_reasoning", "dialectical_cot",
        "root_cause_by_layer", "docs_as_dod",
        "reproducibility_first", "loss_backprop_lens",
        "method_evolution", "drift_detection",
        "iterative_refinement", "per_subtask_e2e",
    }
    t("LAYERS == 12 canonical IDs",
      set(ldd.LAYERS) == expected,
      detail=f"diff={set(ldd.LAYERS) ^ expected}")
    t("using_ldd not in LAYERS (bootstrap, not togglable)",
      "using_ldd" not in ldd.LAYERS)


# ── 2. load_config writes default + hot-reload ─────────────────────────────

def case_load_writes_defaults():
    print("\n[2] load_config writes the default file on first call")
    reset_config()
    p = ldd._config_path()
    t("config absent before first load", not p.exists())
    cfg = ldd.load_config()
    t("config written after first load", p.exists())
    # Default-OFF since the policy switch: LDD soft-discipline does not
    # ship enabled by default. Hard structural enforcement (forge sandbox,
    # skill-forge linter, path-gate, audit hash-chain, promotion gates)
    # lives outside this toggle and stays active regardless.
    t("default enabled=False", cfg.get("enabled") is False)
    t("every layer default=False",
      all(cfg["layers"][layer] is False for layer in ldd.LAYERS))


def case_hot_reload_via_mtime():
    print("\n[3] mtime change triggers re-read")
    reset_config()
    ldd.load_config()  # populate cache
    p = ldd._config_path()
    # Write a different config out-of-band.
    raw = json.loads(p.read_text())
    raw["layers"]["e2e_driven_iteration"] = False
    # Bump mtime so the cache busts even on coarse-grained filesystems.
    p.write_text(json.dumps(raw))
    os.utime(p, (time.time() + 2, time.time() + 2))
    cfg = ldd.load_config()
    t("layers picked up the out-of-band change",
      cfg["layers"]["e2e_driven_iteration"] is False)


# ── 3. Master + per-layer + profile resolution ─────────────────────────────

def case_master_kills_all():
    print("\n[4] cfg.enabled=False forces every layer off")
    reset_config()
    ldd.set_master(False)
    for layer in ldd.LAYERS:
        ok = ldd.is_layer_active(layer) is False
        if not ok:
            t(f"master-off blocks {layer}", False)
            return
    t("master-off blocks every layer", True)


def case_per_layer_persists():
    print("\n[5] per-layer toggle persists round-trip")
    reset_config()
    # Master must be on for per-layer settings to be observable; default
    # is off since the policy switch.
    ldd.set_master(True)
    ldd.set_layer("docs_as_dod", False)
    # Re-enable the other layer explicitly so the cross-layer assertion
    # below is meaningful (default-off would make every layer False).
    ldd.set_layer("e2e_driven_iteration", True)
    cfg = ldd.load_config()
    t("docs_as_dod = False on disk",
      cfg["layers"]["docs_as_dod"] is False)
    t("is_layer_active(docs_as_dod) → False",
      ldd.is_layer_active("docs_as_dod") is False)
    t("other layers untouched",
      ldd.is_layer_active("e2e_driven_iteration") is True)


def case_profile_master_off():
    print("\n[6] profile.ldd_enabled=False kills every layer for that chat")
    reset_config()
    # Flip the global master on AND explicitly enable one layer so the
    # profile-kill is observable against a non-trivial baseline.
    # set_master(True) does not auto-enable per-layer defaults (policy:
    # default-off); set_layer() makes the baseline unambiguously active.
    ldd.set_master(True)
    ldd.set_layer("e2e_driven_iteration", True)
    profile = {"ldd_enabled": False}
    for layer in ldd.LAYERS:
        if ldd.is_layer_active(layer, profile=profile):
            t(f"profile-off blocks {layer}", False)
            return
    t("profile-off blocks every layer", True)
    # Global state stays untouched.
    t("global state still on after profile-off",
      ldd.is_layer_active("e2e_driven_iteration") is True)


def case_profile_per_layer_override():
    print("\n[7] profile.ldd_layers[layer] overrides global per chat")
    reset_config()
    # Set up a non-trivial baseline: master on, docs_as_dod on, e2e off.
    ldd.set_master(True)
    ldd.set_layer("docs_as_dod", True)
    ldd.set_layer("e2e_driven_iteration", False)  # global off
    profile = {"ldd_layers": {"e2e_driven_iteration": True}}  # chat override
    t("profile per-layer beats global",
      ldd.is_layer_active("e2e_driven_iteration", profile=profile) is True)
    # Conversely, per-layer can disable a globally-on layer for one chat.
    profile2 = {"ldd_layers": {"docs_as_dod": False}}
    t("profile per-layer disables a globally-on layer",
      ldd.is_layer_active("docs_as_dod", profile=profile2) is False)
    t("global state for docs_as_dod stays on",
      ldd.is_layer_active("docs_as_dod") is True)


def case_per_layer_beats_profile_master():
    print("\n[8] explicit per-layer beats profile master-off")
    reset_config()
    # Flip global master on so the profile-master-off path is non-trivial.
    ldd.set_master(True)
    profile = {
        "ldd_enabled": False,
        "ldd_layers": {"docs_as_dod": True},
    }
    t("docs_as_dod explicit True wins over profile master-off",
      ldd.is_layer_active("docs_as_dod", profile=profile) is True)
    # Other layers still suppressed by master-off.
    t("other layers still off via profile master",
      ldd.is_layer_active("e2e_driven_iteration", profile=profile) is False)


def case_unknown_layer_fails_open():
    print("\n[9] unknown layer ID returns True (fail-open)")
    reset_config()
    t("typo'd layer-name returns True",
      ldd.is_layer_active("not_a_layer") is True)
    t("empty layer returns True",
      ldd.is_layer_active("") is True)


# ── 4. Presets ─────────────────────────────────────────────────────────────

def case_presets():
    print("\n[10] presets land the expected layer-state on disk")
    reset_config()
    # default
    state = ldd.apply_preset("default")
    t("default: every layer on",
      all(state[l] is True for l in ldd.LAYERS))
    # quick
    state = ldd.apply_preset("quick")
    t("quick: e2e on", state["e2e_driven_iteration"] is True)
    t("quick: docs_as_dod on", state["docs_as_dod"] is True)
    t("quick: per_subtask_e2e on", state["per_subtask_e2e"] is True)
    t("quick: dialectical_reasoning on",
      state["dialectical_reasoning"] is True)
    t("quick: loop_driven_engineering off",
      state["loop_driven_engineering"] is False)
    t("quick: method_evolution off",
      state["method_evolution"] is False)
    # off
    state = ldd.apply_preset("off")
    t("off: every layer off",
      all(state[l] is False for l in ldd.LAYERS))
    cfg = ldd.load_config()
    t("off: master also disabled",
      cfg["enabled"] is False)
    # unknown preset raises
    raised = False
    try:
        ldd.apply_preset("nope")
    except ValueError:
        raised = True
    t("unknown preset raises ValueError", raised)


# ── 5. Skill-name → layer mapping ──────────────────────────────────────────

def case_skill_name_mapping():
    print("\n[11] skill-name → layer mapping handles every input form")
    cases = [
        ("loop-driven-engineering",                          "loop_driven_engineering"),
        ("loop_driven_engineering",                          "loop_driven_engineering"),
        ("E2E-Driven-Iteration",                             "e2e_driven_iteration"),
        ("dialectical-reasoning",                            "dialectical_reasoning"),
        ("loss-driven-development:dialectical-reasoning",    "dialectical_reasoning"),
        ("docs-as-definition-of-done",                       "docs_as_dod"),  # alias
        ("docs_as_dod",                                      "docs_as_dod"),
        ("per-subtask-e2e",                                  "per_subtask_e2e"),
        ("frontend-design",                                  None),  # unknown
        ("trading.score_reviews",                            None),  # unknown
        ("",                                                 None),
    ]
    for inp, expected in cases:
        got = ldd.layer_for_skill_name(inp)
        t(f"layer_for_skill_name({inp!r}) → {expected!r}",
          got == expected, detail=f"got={got!r}")


# ── 6. filter_skills ───────────────────────────────────────────────────────

class _Spec:
    def __init__(self, name: str):
        self.name = name


def case_filter_skills():
    print("\n[12] filter_skills drops LDD-skills whose layer is off")
    reset_config()
    # Master on + selectively flip two layers off, one explicitly on.
    # Without master-on, default-off would suppress everything and the
    # "kept (layer on)" assertion below would be vacuously false.
    ldd.set_master(True)
    ldd.set_layer("e2e_driven_iteration", False)
    ldd.set_layer("docs_as_dod", False)
    ldd.set_layer("dialectical_reasoning", True)
    specs = [
        _Spec("e2e-driven-iteration"),     # OFF (LDD layer)
        _Spec("docs-as-definition-of-done"),  # OFF (LDD layer alias)
        _Spec("dialectical-reasoning"),    # ON (LDD layer)
        _Spec("frontend-design"),          # not-LDD: passes through
        _Spec("trading.score_reviews"),    # not-LDD: passes through
    ]
    out = ldd.filter_skills(specs)
    names = [s.name for s in out]
    t("e2e-driven-iteration filtered out",
      "e2e-driven-iteration" not in names)
    t("docs-as-definition-of-done filtered out (alias)",
      "docs-as-definition-of-done" not in names)
    t("dialectical-reasoning kept (layer on)",
      "dialectical-reasoning" in names)
    t("non-LDD skill frontend-design passes through",
      "frontend-design" in names)
    t("non-LDD skill trading.score_reviews passes through",
      "trading.score_reviews" in names)
    # profile-override: docs_as_dod re-enabled for one chat
    profile = {"ldd_layers": {"docs_as_dod": True}}
    out2 = ldd.filter_skills(specs, profile=profile)
    names2 = [s.name for s in out2]
    t("profile override re-enables docs_as_dod skill",
      "docs-as-definition-of-done" in names2)


# ── 7. CLI round-trip ──────────────────────────────────────────────────────

def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "operator/bridges/shared/ldd.py"),
         *args],
        capture_output=True, text=True, env={**os.environ},
    )


def case_cli_status():
    print("\n[13] CLI: status, on, off, set, preset")
    reset_config()
    r = _run_cli("status")
    t("status exit 0", r.returncode == 0)
    # Default-OFF since the policy switch.
    t("status mentions enabled=False",
      "enabled=False" in r.stdout)
    t("status lists every layer",
      all(layer in r.stdout for layer in ldd.LAYERS))


def case_cli_on_off():
    reset_config()
    r = _run_cli("off")
    t("off exit 0", r.returncode == 0)
    cfg = json.loads(ldd._config_path().read_text())
    t("off persisted enabled=false",
      cfg.get("enabled") is False)
    r = _run_cli("on")
    t("on exit 0", r.returncode == 0)
    cfg = json.loads(ldd._config_path().read_text())
    t("on persisted enabled=true",
      cfg.get("enabled") is True)


def case_cli_set():
    reset_config()
    # Hyphenated form is accepted (auto-canonicalized).
    r = _run_cli("set", "loop-driven-engineering", "off")
    t("set hyphenated layer exit 0", r.returncode == 0)
    cfg = json.loads(ldd._config_path().read_text())
    t("set persisted loop_driven_engineering=False",
      cfg["layers"]["loop_driven_engineering"] is False)
    r = _run_cli("set", "loop_driven_engineering", "on")
    cfg = json.loads(ldd._config_path().read_text())
    t("set on flips it back",
      cfg["layers"]["loop_driven_engineering"] is True)
    # Unknown layer rejected.
    r = _run_cli("set", "not_a_layer", "off")
    t("unknown layer exits non-zero",
      r.returncode != 0)
    t("unknown layer mentions valid list",
      "valid:" in r.stdout)


def case_cli_preset():
    reset_config()
    r = _run_cli("preset", "quick")
    t("preset quick exit 0", r.returncode == 0)
    cfg = json.loads(ldd._config_path().read_text())
    t("preset quick: e2e on",
      cfg["layers"]["e2e_driven_iteration"] is True)
    t("preset quick: drift_detection off",
      cfg["layers"]["drift_detection"] is False)
    r = _run_cli("preset", "off")
    cfg = json.loads(ldd._config_path().read_text())
    t("preset off: enabled=False",
      cfg.get("enabled") is False)


# ── 8. LDD_AUTO_OPTIN env-var activation ──────────────────────────────────

def case_ldd_auto_optin():
    print("\n[15] LDD_AUTO_OPTIN=1 activates master + all layers (overrides file)")
    reset_config()
    ldd.set_master(False)  # file says off

    old = os.environ.pop("LDD_AUTO_OPTIN", None)
    try:
        # Without env var: should be off (file says disabled).
        t("master_enabled=False without LDD_AUTO_OPTIN",
          ldd.master_enabled() is False)
        t("is_layer_active=False without LDD_AUTO_OPTIN",
          ldd.is_layer_active("loop_driven_engineering") is False)

        # With env var: file is disabled but env overrides.
        os.environ["LDD_AUTO_OPTIN"] = "1"
        t("master_enabled=True with LDD_AUTO_OPTIN=1",
          ldd.master_enabled() is True)
        for layer in ldd.LAYERS:
            ok = ldd.is_layer_active(layer) is True
            if not ok:
                t(f"LDD_AUTO_OPTIN enables {layer}", False)
                return
        t("LDD_AUTO_OPTIN=1 enables all 12 layers", True)

        # Explicit profile.ldd_enabled=False still wins (persona-level kill).
        t("profile.ldd_enabled=False overrides LDD_AUTO_OPTIN",
          ldd.master_enabled(profile={"ldd_enabled": False}) is False)
        t("profile.ldd_enabled=False blocks all layers even with LDD_AUTO_OPTIN",
          ldd.is_layer_active("loop_driven_engineering",
                               profile={"ldd_enabled": False}) is False)

        # Explicit profile per-layer disable still wins.
        t("profile per-layer disable beats LDD_AUTO_OPTIN",
          ldd.is_layer_active(
              "e2e_driven_iteration",
              profile={"ldd_layers": {"e2e_driven_iteration": False}},
          ) is False)

        # effective_state() should agree with is_layer_active() and return
        # reason "on" when the env var is the activation source.
        active, reason = ldd.effective_state("loop_driven_engineering")
        t("effective_state active=True with LDD_AUTO_OPTIN=1", active is True)
        t("effective_state reason='on' with LDD_AUTO_OPTIN=1", reason == "on")
        # Cascade parents activated by env var must still gate the child.
        active_child, _ = ldd.effective_state("dialectical_cot")
        parent_active = ldd.is_layer_active("dialectical_reasoning")
        t("cascade holds with LDD_AUTO_OPTIN=1 (dialectical_cot follows parent)",
          active_child is parent_active)
    finally:
        # Restore environment state.
        if old is not None:
            os.environ["LDD_AUTO_OPTIN"] = old
        else:
            os.environ.pop("LDD_AUTO_OPTIN", None)


# ── 9. CI-lint: import anthropic forbidden ─────────────────────────────────

def case_no_anthropic_sdk_import():
    print("\n[14] ldd.py must NOT import the Anthropic SDK")
    import ast
    src_path = REPO / "operator/bridges/shared/ldd.py"
    tree = ast.parse(src_path.read_text())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "anthropic":
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "anthropic":
                bad.append(f"from {node.module} import ...")
    t("no `import anthropic` AST node",
      not bad, detail=f"found={bad}")


def main():
    case_layer_set()
    case_load_writes_defaults()
    case_hot_reload_via_mtime()
    case_master_kills_all()
    case_per_layer_persists()
    case_profile_master_off()
    case_profile_per_layer_override()
    case_per_layer_beats_profile_master()
    case_unknown_layer_fails_open()
    case_presets()
    case_skill_name_mapping()
    case_filter_skills()
    case_cli_status()
    case_cli_on_off()
    case_cli_set()
    case_cli_preset()
    case_ldd_auto_optin()
    case_no_anthropic_sdk_import()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
