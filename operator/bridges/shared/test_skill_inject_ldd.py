#!/usr/bin/env python3
"""E2E tests for the LDD-toggle filter inside skill_inject (Layer 14).

Drives the real skill-forge MultiSkillRegistry against a tempdir
CORVIN_HOME, creates LDD-named + non-LDD-named skills, grades them so
they become eligible for injection, and asserts that the filter:
  - master ON, all layers ON   → every skill present
  - layer OFF                  → just that one skill is filtered, others stay
  - master OFF                 → every LDD-named skill filtered, non-LDD passes
  - profile.ldd_layers override → re-enables a specific layer per chat
  - profile.ldd_enabled=False  → master kill per chat
  - auto_grade respects the same filter (no grades flow to OFF layers)

Run as: python3 operator/bridges/shared/test_skill_inject_ldd.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "skill-forge"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Sandbox BEFORE importing — every plugin's path resolution depends on env.
_TD = Path(tempfile.mkdtemp(prefix="ldd-skill-inject-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(_TD / "slot")
# Don't let the shell's global LDD_AUTO_OPTIN=1 (~/.bashrc) override the
# file-based toggles under test — mirrors test_ldd_lib.py.
os.environ.pop("LDD_AUTO_OPTIN", None)

import ldd  # noqa: E402
import skill_inject  # noqa: E402
from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_ldd_config():
    """Wipe LDD config + cache, then flip to ALL-ON baseline.

    Default-OFF since the policy switch — but this test-suite was written
    when default-on was the convention. To keep the test intent intact
    (LDD-on baseline, then flip individual layers / master off), the
    reset helper now restores a master-on / every-layer-on state. Tests
    that need master-off or layer-off call ``ldd.set_master(False)`` /
    ``ldd.set_layer(..., False)`` explicitly after this.
    """
    p = ldd._config_path()
    if p.exists():
        p.unlink()
    ldd._CONFIG_CACHE = {}
    ldd._CONFIG_MTIME = 0.0
    ldd.set_master(True)
    for layer in ldd.LAYERS:
        ldd.set_layer(layer, True)


# Body large enough to comfortably clear the linter's density check.
_BODY = (
    "This is the body of a SkillForge skill used by the layer-14 filter "
    "test. It contains multiple sentences that describe what the skill "
    "would do in production. The body is intentionally prose-heavy so the "
    "linter's code-density check does not trip on it.\n\n"
    "Second paragraph: more prose, more sentences, more good advice. The "
    "filter test does not care about content — only that the skill exists "
    "and has at least one positive grade.\n"
)


def _make_skills(reg: MultiSkillRegistry) -> None:
    """Three test skills: two are LDD-layer matches, one is a plain
    domain skill the filter must always let through."""
    reg.create(
        name="e2e_driven_iteration", type="domain",
        body_md=_BODY, description="LDD layer e2e",
    )
    reg.create(
        name="dialectical_reasoning", type="domain",
        body_md=_BODY, description="LDD layer dialectical",
    )
    reg.create(
        name="frontend_design", type="domain",
        body_md=_BODY, description="non-LDD domain skill",
    )
    # Grade every skill so they become eligible for injection (default
    # filter excludes ungraded skills).
    for n in ("e2e_driven_iteration", "dialectical_reasoning", "frontend_design"):
        reg.grade(n, run_id=f"setup-{n}", score=0.7)


def _block_names(block: str | None) -> set[str]:
    if not block:
        return set()
    out = set()
    for line in block.splitlines():
        if line.startswith("<auto_skill ") and 'name="' in line:
            start = line.index('name="') + len('name="')
            end = line.index('"', start)
            out.add(line[start:end])
    return out


# ── Test cases ─────────────────────────────────────────────────────────────


def case_baseline_all_present():
    print("\n[1] master ON, all layers ON → every skill in the inject block")
    reset_ldd_config()
    block = skill_inject.collect_active_skills(
        channel_id="bridge:test1", profile=None,
    )
    names = _block_names(block)
    t("e2e_driven_iteration in block", "e2e_driven_iteration" in names)
    t("dialectical_reasoning in block", "dialectical_reasoning" in names)
    t("frontend_design in block", "frontend_design" in names)


def case_per_layer_off():
    print("\n[2] one layer OFF → exactly that skill is filtered out")
    reset_ldd_config()
    ldd.set_layer("e2e_driven_iteration", False)
    block = skill_inject.collect_active_skills(
        channel_id="bridge:test2", profile=None,
    )
    names = _block_names(block)
    t("e2e_driven_iteration filtered out",
      "e2e_driven_iteration" not in names)
    t("dialectical_reasoning still in (layer on)",
      "dialectical_reasoning" in names)
    t("frontend_design still in (non-LDD)",
      "frontend_design" in names)


def case_master_off():
    print("\n[3] master OFF → every LDD-named skill filtered, non-LDD passes")
    reset_ldd_config()
    ldd.set_master(False)
    block = skill_inject.collect_active_skills(
        channel_id="bridge:test3", profile=None,
    )
    names = _block_names(block)
    t("e2e_driven_iteration filtered (master off)",
      "e2e_driven_iteration" not in names)
    t("dialectical_reasoning filtered (master off)",
      "dialectical_reasoning" not in names)
    t("frontend_design still in (non-LDD passes)",
      "frontend_design" in names)


def case_profile_override_re_enables_layer():
    print("\n[4] profile.ldd_layers override re-enables a layer for one chat")
    reset_ldd_config()
    ldd.set_layer("dialectical_reasoning", False)  # global off
    profile = {"ldd_layers": {"dialectical_reasoning": True}}
    block = skill_inject.collect_active_skills(
        channel_id="bridge:test4", profile=profile,
    )
    names = _block_names(block)
    t("dialectical_reasoning re-enabled via profile",
      "dialectical_reasoning" in names)
    t("e2e_driven_iteration still in (default on)",
      "e2e_driven_iteration" in names)
    t("frontend_design still in",
      "frontend_design" in names)


def case_profile_master_off_per_chat():
    print("\n[5] profile.ldd_enabled=False is a per-chat master kill")
    reset_ldd_config()
    profile = {"ldd_enabled": False}
    block = skill_inject.collect_active_skills(
        channel_id="bridge:test5", profile=profile,
    )
    names = _block_names(block)
    t("e2e_driven_iteration filtered for this chat",
      "e2e_driven_iteration" not in names)
    t("dialectical_reasoning filtered for this chat",
      "dialectical_reasoning" not in names)
    t("frontend_design still in",
      "frontend_design" in names)
    # And global state stays untouched.
    block_other = skill_inject.collect_active_skills(
        channel_id="bridge:test5b", profile=None,
    )
    names_other = _block_names(block_other)
    t("global chat unaffected — e2e_driven_iteration still present",
      "e2e_driven_iteration" in names_other)


def case_auto_grade_respects_filter():
    print("\n[6] auto_grade does not score skills whose layer is OFF")
    reset_ldd_config()
    ldd.set_layer("e2e_driven_iteration", False)
    # Output mentions the e2e skill name AND the dialectical skill name.
    output = (
        "I followed the e2e driven iteration loop and then applied "
        "dialectical reasoning to the recommendation."
    )
    graded = skill_inject.auto_grade_from_output(
        channel_id="bridge:test6",
        profile=None,
        output_text=output,
        run_id="auto-grade-run",
    )
    names = {g["name"] for g in graded}
    t("e2e_driven_iteration NOT auto-graded (layer off)",
      "e2e_driven_iteration" not in names)
    t("dialectical_reasoning auto-graded (layer on, mentioned)",
      "dialectical_reasoning" in names,
      detail=f"graded={names}")


def main():
    reg = MultiSkillRegistry(channel_id=None, project_root=None)
    _make_skills(reg)
    case_baseline_all_present()
    case_per_layer_off()
    case_master_off()
    case_profile_override_re_enables_layer()
    case_profile_master_off_per_chat()
    case_auto_grade_respects_filter()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
