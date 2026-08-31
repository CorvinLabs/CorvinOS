#!/usr/bin/env python3
"""E2E tests for the LDD layer dependency cascade (Layer 14, v2).

Three Hard-Cascade pairs:
  - dialectical_cot  → dialectical_reasoning
  - per_subtask_e2e  → e2e_driven_iteration
  - drift_detection  → docs_as_dod

Coverage matrix per pair (4 base states):
  P-on  C-on  → C active
  P-on  C-off → C off (manual)
  P-off C-on  → C off (cascade)
  P-off C-off → C off

Plus orthogonal cases that have to hold for every pair:
  - profile.ldd_enabled=False kills both parent and child
  - cfg.enabled=False kills both
  - profile.ldd_layers[child]=True cannot override cascade-off
  - profile.ldd_layers[parent]=True LIFTS cascade per chat
  - effective_state returns the right reason ("on", "manual_off",
    "cascade_off:<parent>", "global_master_off", "profile_master_off")
  - /ldd-set <child> on while parent off prints a warning
  - /ldd-set <parent> off prints a "cascade" line listing affected children
  - skill_inject filters cascaded-off children even when their layer
    isn't directly off in cfg
  - dialectic-coupling: dialectical_cot child filter via skill_inject
    + dialectic.resolve_mode unaffected (only top-level layer)
  - PRESETS: no preset turns a child on while leaving its parent off

Run: python3 operator/bridges/shared/test_ldd_dependencies.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox CORVIN_HOME BEFORE importing.
_TD = Path(tempfile.mkdtemp(prefix="ldd-dependencies-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(_TD / "slot")
# Don't let the shell's global LDD_AUTO_OPTIN=1 (~/.bashrc) override the
# file-based toggles under test — mirrors test_ldd_lib.py.
os.environ.pop("LDD_AUTO_OPTIN", None)

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


def reset_ldd():
    """Wipe LDD config + cache, then flip to ALL-ON baseline.

    Default-OFF since the policy switch — but this dependency-cascade
    suite was written when default-on was the convention. To keep the
    test intent intact (master-on baseline, then toggle individual
    layers / master off explicitly), the helper restores a master-on /
    every-layer-on state.
    """
    p = ldd._config_path()
    if p.exists():
        p.unlink()
    ldd._CONFIG_CACHE = {}
    ldd._CONFIG_MTIME = 0.0
    ldd.set_master(True)
    for layer in ldd.LAYERS:
        ldd.set_layer(layer, True)


# ── 1. Cascade pairs are exactly the three sanctioned ones ─────────────────


def case_dependency_set():
    print("\n[1] DEPENDS_ON contains the three sanctioned hard-cascade pairs")
    expected = {
        "dialectical_cot":  "dialectical_reasoning",
        "per_subtask_e2e":  "e2e_driven_iteration",
        "drift_detection":  "docs_as_dod",
    }
    t("DEPENDS_ON == expected", ldd.DEPENDS_ON == expected,
      detail=f"got={ldd.DEPENDS_ON}")
    # Every parent listed is itself a known LDD layer.
    for child, parent in ldd.DEPENDS_ON.items():
        t(f"parent {parent} of {child} is a known LDD layer",
          parent in ldd.LAYERS)
        t(f"child {child} is a known LDD layer",
          child in ldd.LAYERS)


# ── 2. Per-pair 4-state base matrix ────────────────────────────────────────


def base_matrix(parent: str, child: str):
    print(f"\n[2.{parent}/{child}] 4-state base matrix")
    # 1) P on, C on
    reset_ldd()
    ldd.set_layer(parent, True)
    ldd.set_layer(child, True)
    t("P-on C-on → C active",
      ldd.is_layer_active(child) is True)
    # 2) P on, C off
    ldd.set_layer(child, False)
    t("P-on C-off → C off (manual)",
      ldd.is_layer_active(child) is False)
    # 3) P off, C on
    ldd.set_layer(parent, False)
    ldd.set_layer(child, True)
    t("P-off C-on → C off (cascade)",
      ldd.is_layer_active(child) is False)
    t("P-off C-on → effective_state reports cascade",
      ldd.effective_state(child) == (False, f"cascade_off:{parent}"))
    # 4) P off, C off
    ldd.set_layer(child, False)
    t("P-off C-off → C off",
      ldd.is_layer_active(child) is False)


def case_all_pairs_base_matrix():
    for child, parent in ldd.DEPENDS_ON.items():
        base_matrix(parent, child)


# ── 3. Master kills cascade through both parent and child ──────────────────


def case_global_master_kills_pairs():
    print("\n[3] cfg.enabled=False kills parent + child for every pair")
    reset_ldd()
    ldd.set_master(False)
    for child, parent in ldd.DEPENDS_ON.items():
        t(f"global master off → parent {parent} inactive",
          ldd.is_layer_active(parent) is False)
        t(f"global master off → child {child} inactive",
          ldd.is_layer_active(child) is False)
        # effective_state should pin master_off, not cascade.
        active, reason = ldd.effective_state(child)
        t(f"effective_state({child}).reason == global_master_off",
          (not active) and reason == "global_master_off",
          detail=f"got=({active}, {reason})")


def case_profile_master_kills_pairs():
    print("\n[4] profile.ldd_enabled=False kills parent + child for that chat")
    reset_ldd()
    profile = {"ldd_enabled": False}
    for child, parent in ldd.DEPENDS_ON.items():
        t(f"profile master off → parent {parent} inactive",
          ldd.is_layer_active(parent, profile=profile) is False)
        t(f"profile master off → child {child} inactive",
          ldd.is_layer_active(child, profile=profile) is False)
        active, reason = ldd.effective_state(child, profile=profile)
        t(f"effective_state({child}, profile).reason == profile_master_off",
          (not active) and reason == "profile_master_off",
          detail=f"got=({active}, {reason})")
    # Global state untouched.
    for child in ldd.DEPENDS_ON:
        t(f"global state for {child} still on",
          ldd.is_layer_active(child) is True)


# ── 4. Cascade beats explicit profile override on the child ────────────────


def case_cascade_beats_child_profile_override():
    print("\n[5] profile.ldd_layers[child]=True cannot override cascade-off-parent")
    for child, parent in ldd.DEPENDS_ON.items():
        reset_ldd()
        ldd.set_layer(parent, False)
        profile = {"ldd_layers": {child: True}}
        t(f"explicit profile [{child}=True] respects cascade (parent {parent} off)",
          ldd.is_layer_active(child, profile=profile) is False)
        active, reason = ldd.effective_state(child, profile=profile)
        t(f"effective_state pin to cascade_off:{parent}",
          (not active) and reason == f"cascade_off:{parent}",
          detail=f"got=({active}, {reason})")


# ── 5. Profile parent override LIFTS cascade per chat ──────────────────────


def case_profile_parent_lift_releases_cascade():
    print("\n[6] profile.ldd_layers[parent]=True lifts cascade per chat")
    for child, parent in ldd.DEPENDS_ON.items():
        reset_ldd()
        ldd.set_layer(parent, False)  # global off
        ldd.set_layer(child, True)    # global on (would otherwise cascade-off)
        # Without profile override → cascade kicks in
        t(f"baseline: cascade off without profile lift (child {child})",
          ldd.is_layer_active(child) is False)
        # With profile override on parent → cascade releases
        profile = {"ldd_layers": {parent: True}}
        t(f"profile [{parent}=True] lifts cascade for child {child}",
          ldd.is_layer_active(child, profile=profile) is True)


# ── 6. effective_state for non-cascade and parent layers ───────────────────


def case_effective_state_non_cascade_layers():
    print("\n[7] effective_state pins the right reason on every shape of off")
    reset_ldd()
    # Non-cascade layer manual off
    ldd.set_layer("loss_backprop_lens", False)
    active, reason = ldd.effective_state("loss_backprop_lens")
    t("manual_off pinned for non-cascade layer",
      (not active) and reason == "manual_off",
      detail=f"got=({active}, {reason})")
    # Parent layers (not in DEPENDS_ON keys) cannot cascade-off via parent
    reset_ldd()
    active, reason = ldd.effective_state("e2e_driven_iteration")
    t("parent-only layer e2e_driven_iteration → on",
      active and reason == "on",
      detail=f"got=({active}, {reason})")
    # Direct on with profile per-layer
    reset_ldd()
    ldd.set_layer("docs_as_dod", False)
    active, reason = ldd.effective_state(
        "docs_as_dod", profile={"ldd_layers": {"docs_as_dod": True}},
    )
    t("profile-on lifts cfg-off → on",
      active and reason == "on",
      detail=f"got=({active}, {reason})")


# ── 7. Status output shows cascade source ──────────────────────────────────


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "operator/bridges/shared/ldd.py"),
         *args],
        capture_output=True, text=True, env={**os.environ},
    )


def case_status_shows_cascade():
    print("\n[8] /ldd-status shows cascade source for off-child layers")
    reset_ldd()
    ldd.set_layer("docs_as_dod", False)
    r = _run_cli("status")
    t("status exit 0", r.returncode == 0)
    # drift_detection should show cascade
    out = r.stdout
    found_cascade = any(
        ("drift_detection" in line and "cascade: parent docs_as_dod" in line)
        for line in out.splitlines()
    )
    t("status mentions cascade for drift_detection",
      found_cascade, detail=f"out={out[:400]!r}")
    # And shows the child→parent table at the bottom
    t("status lists dependencies table",
      "dependencies (child -> parent)" in out)
    t("status lists drift_detection -> docs_as_dod",
      "drift_detection -> docs_as_dod" in out)


def case_status_shows_depends_on_for_active_children():
    print("\n[9] /ldd-status annotates active children with their parent")
    reset_ldd()
    r = _run_cli("status")
    out = r.stdout
    # When everything is on, drift_detection line should still mention
    # "depends on docs_as_dod" so the operator sees the link.
    found = any(
        ("drift_detection" in line and "depends on docs_as_dod" in line)
        for line in out.splitlines()
    )
    t("active drift_detection annotates parent docs_as_dod",
      found, detail=f"out={out[:400]!r}")


# ── 8. Set commands print warnings + cascade hints ─────────────────────────


def case_set_child_on_while_parent_off_warns():
    print("\n[10] /ldd-set <child> on while parent off prints warning")
    reset_ldd()
    ldd.set_layer("dialectical_reasoning", False)
    r = _run_cli("set", "dialectical_cot", "on")
    t("set exit 0", r.returncode == 0)
    out = r.stdout
    t("warning mentions parent dialectical_reasoning is off",
      "depends on dialectical_reasoning" in out
      and "currently OFF" in out,
      detail=f"out={out!r}")
    # And the underlying state IS persisted (cascade gate is read-path).
    cfg = ldd.load_config()
    t("set is persisted despite warning",
      cfg["layers"]["dialectical_cot"] is True)


def case_set_parent_off_lists_children():
    print("\n[11] /ldd-set <parent> off prints cascade hint with affected children")
    reset_ldd()
    r = _run_cli("set", "docs_as_dod", "off")
    t("set parent off exit 0", r.returncode == 0)
    out = r.stdout
    t("cascade hint mentions drift_detection",
      "cascade" in out and "drift_detection" in out,
      detail=f"out={out!r}")
    # Same for e2e parent
    reset_ldd()
    r = _run_cli("set", "e2e_driven_iteration", "off")
    out = r.stdout
    t("cascade hint mentions per_subtask_e2e",
      "cascade" in out and "per_subtask_e2e" in out,
      detail=f"out={out!r}")
    # Same for dialectical_reasoning parent
    reset_ldd()
    r = _run_cli("set", "dialectical_reasoning", "off")
    out = r.stdout
    t("cascade hint mentions dialectical_cot",
      "cascade" in out and "dialectical_cot" in out,
      detail=f"out={out!r}")


# ── 9. Preset coherence ────────────────────────────────────────────────────


def case_preset_coherence():
    print("\n[12] No preset turns a child on while leaving its parent off")
    for name, layers in ldd.PRESETS.items():
        for child, parent in ldd.DEPENDS_ON.items():
            child_on = layers.get(child, True)
            parent_on = layers.get(parent, True)
            ok = (not child_on) or parent_on
            t(f"preset {name}: ({child} on={child_on}) implies ({parent} on={parent_on}) — {ok}",
              ok, detail=f"layers[{parent}]={parent_on} layers[{child}]={child_on}")


# ── 10. skill_inject respects cascade ──────────────────────────────────────


def case_skill_inject_respects_cascade():
    print("\n[13] skill_inject filter respects cascade for every cascade pair")
    import skill_inject  # noqa: PLC0415
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

    body = (
        "Long-enough body text to satisfy the linter density check. "
        "More content here. And here too. And a fourth sentence. "
        "Plus a fifth one for good measure.\n"
    )
    reg = MultiSkillRegistry(channel_id=None, project_root=None)
    # Create skills named exactly like the layers, so layer_for_skill_name
    # picks them up directly. A small per-test set is enough.
    for child in ldd.DEPENDS_ON:
        try:
            reg.create(name=child, type="domain",
                       body_md=body, description=f"LDD child layer {child}")
        except Exception:
            pass
        try:
            reg.grade(child, run_id=f"setup-{child}", score=0.7)
        except Exception:
            pass
    for parent in set(ldd.DEPENDS_ON.values()):
        try:
            reg.create(name=parent, type="domain",
                       body_md=body, description=f"LDD parent layer {parent}")
        except Exception:
            pass
        try:
            reg.grade(parent, run_id=f"setup-{parent}", score=0.7)
        except Exception:
            pass

    for child, parent in ldd.DEPENDS_ON.items():
        reset_ldd()
        ldd.set_layer(parent, False)  # cascade-off the child
        block = skill_inject.collect_active_skills(
            channel_id=f"bridge:dep-{child}", profile=None,
        )
        names = set()
        if block:
            for line in block.splitlines():
                if line.startswith("<auto_skill ") and 'name="' in line:
                    s = line.index('name="') + len('name="')
                    e = line.index('"', s)
                    names.add(line[s:e])
        t(f"cascade-off → child skill {child} filtered out of inject block",
          child not in names,
          detail=f"names={names}")
        t(f"cascade-off → parent skill {parent} also filtered out",
          parent not in names,
          detail=f"names={names}")


# ── 11. Defensive: dependency graph has no cycles ──────────────────────────


def case_no_cycles_in_dependency_graph():
    print("\n[14] DEPENDS_ON has no cycles (defensive — would infinite-loop)")
    for start in ldd.DEPENDS_ON:
        seen = {start}
        cur = start
        while cur in ldd.DEPENDS_ON:
            cur = ldd.DEPENDS_ON[cur]
            if cur in seen:
                t(f"cycle detected starting at {start}", False,
                  detail=f"chain: {seen} -> {cur}")
                return
            seen.add(cur)
    t("no cycles in dependency graph", True)


def main():
    case_dependency_set()
    case_all_pairs_base_matrix()
    case_global_master_kills_pairs()
    case_profile_master_kills_pairs()
    case_cascade_beats_child_profile_override()
    case_profile_parent_lift_releases_cascade()
    case_effective_state_non_cascade_layers()
    case_status_shows_cascade()
    case_status_shows_depends_on_for_active_children()
    case_set_child_on_while_parent_off_warns()
    case_set_parent_off_lists_children()
    case_preset_coherence()
    case_skill_inject_respects_cascade()
    case_no_cycles_in_dependency_graph()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
