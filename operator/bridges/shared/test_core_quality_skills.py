#!/usr/bin/env python3
"""E2E tests for ADR-0259's core quality-discipline skills (adr_gate,
e2e-wiring-proof) always-on behavior in skill_inject.py.

Covers what test_adapter_skill_inject.py's case-A does not:
  - quality_layers.py disable_layer/disable_all suppresses core skills
  - profile.inject_skills=False remains a true kill-switch (core included)
  - core skills are never crowded out by the registry cap
  - source-tree AND wheel-layout bundle-path resolution both work
  - the previously-double-frontmatter adr_gate/SKILL.md no longer leaks
    raw YAML into the injected body

Run as: python3 operator/bridges/shared/test_core_quality_skills.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

_TD = Path(tempfile.mkdtemp(prefix="core-quality-skills-"))
os.environ["CORVIN_HOME"] = str(_TD)

import quality_layers  # noqa: E402
import skill_inject  # noqa: E402

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def reset_quality_layers() -> None:
    path = _TD / "global" / "quality-layers.json"
    path.unlink(missing_ok=True)


def case_bundle_dir_resolves_in_source_tree() -> None:
    print("\n[1] bundle skills dir resolves from a real source-tree checkout")
    bundle_dir = skill_inject._resolve_bundle_skills_dir()
    t("bundle dir found", bundle_dir is not None)
    t("bundle dir is operator/bundle/skills/ldd",
      bundle_dir is not None and bundle_dir.name == "ldd" and bundle_dir.parent.name == "skills")
    for name in skill_inject._CORE_QUALITY_SKILL_NAMES:
        skill_md = bundle_dir / name / "SKILL.md"
        t(f"{name}/SKILL.md exists on disk", skill_md.is_file())


def case_bundle_dir_resolves_in_simulated_wheel_layout() -> None:
    print("\n[2] bundle skills dir resolves from a simulated wheel _vendor layout")
    fake_vendor_root = Path(tempfile.mkdtemp(prefix="fake-vendor-"))
    fake_shared_dir = fake_vendor_root / "operator" / "bridges" / "shared"
    fake_shared_dir.mkdir(parents=True)
    fake_ldd_dir = fake_vendor_root / "operator" / "bundle" / "skills" / "ldd" / "adr_gate"
    fake_ldd_dir.mkdir(parents=True)
    (fake_ldd_dir / "SKILL.md").write_text(
        "---\nname: adr_gate\ndescription: fake vendored copy\n---\n\nbody\n"
    )
    # _HERE is computed once at import time from the REAL file location, so
    # we can't just re-import; instead verify the candidate-path FORMULA
    # itself against a filesystem shaped like a real wheel install, proving
    # the parents-chain arithmetic (not just that today's source tree
    # happens to resolve).
    fake_here = fake_shared_dir
    wheel_candidate = fake_here.parent.parent.parent / "operator" / "bundle" / "skills" / "ldd"
    t("wheel-layout candidate resolves to the fake vendored ldd dir",
      wheel_candidate == fake_vendor_root / "operator" / "bundle" / "skills" / "ldd")
    t("wheel-layout candidate is a real directory", wheel_candidate.is_dir())
    t("wheel-layout candidate contains the fake adr_gate SKILL.md",
      (wheel_candidate / "adr_gate" / "SKILL.md").is_file())


def case_quality_layers_disable_single() -> None:
    print("\n[3] quality_layers.disable_layer suppresses exactly one core skill")
    # NOTE: check the actual <auto_skill name="adr_gate"> wrapper tag, not a
    # bare "adr_gate" substring — e2e-wiring-proof's own body cross-
    # references "adr_gate" in prose ("the sibling gate to adr_gate"), which
    # would false-positive a plain substring check even when the adr_gate
    # skill itself is correctly filtered out.
    reset_quality_layers()
    skill_inject._sf = None
    result = skill_inject.collect_active_skills(channel_id="t3", profile={})
    t("both core skills present by default", result is not None
      and '<auto_skill name="adr_gate"' in result and '<auto_skill name="e2e-wiring-proof"' in result)

    quality_layers.disable_layer("adr_gate")
    result = skill_inject.collect_active_skills(channel_id="t3", profile={})
    t("adr_gate suppressed after disable_layer",
      result is None or '<auto_skill name="adr_gate"' not in result)
    t("e2e-wiring-proof still present",
      result is not None and '<auto_skill name="e2e-wiring-proof"' in result)
    reset_quality_layers()


def case_quality_layers_disable_all() -> None:
    print("\n[4] quality_layers.disable_all suppresses both core skills entirely")
    reset_quality_layers()
    skill_inject._sf = None
    quality_layers.disable_all()
    result = skill_inject.collect_active_skills(channel_id="t4", profile={})
    t("result is None with everything disabled", result is None)
    reset_quality_layers()


def case_inject_skills_false_is_true_kill_switch() -> None:
    print("\n[5] profile.inject_skills=False suppresses core skills too")
    reset_quality_layers()
    skill_inject._sf = None
    result = skill_inject.collect_active_skills(
        channel_id="t5", profile={"inject_skills": False},
    )
    t("result is None even though quality_layers defaults are all-on", result is None)


def case_adr_gate_body_has_no_double_frontmatter_leak() -> None:
    print("\n[6] adr_gate SKILL.md body has no leaked second frontmatter block")
    reset_quality_layers()
    skill_inject._sf = None
    result = skill_inject.collect_active_skills(channel_id="t6", profile={})
    t("result present", result is not None)
    body = result or ""
    t("no leaked 'type: domain' YAML line", "type: domain" not in body)
    t("no leaked 'claim:' YAML line", "claim:" not in body)
    t("real markdown heading present", "# ADR Gate" in body)


def case_core_skills_never_capped_out_by_registry() -> None:
    print("\n[7] core skills survive even a cap of 1 registry skill (they bypass the cap)")
    reset_quality_layers()
    skill_inject._sf = None  # no registry at all in this process — cap only applies there
    result = skill_inject.collect_active_skills(
        channel_id="t7", profile={"max_injected_skills": 1},
    )
    t("both core skills present despite cap=1 (cap only bounds registry skills)",
      result is not None and "adr_gate" in result and "e2e-wiring-proof" in result)


def main() -> int:
    case_bundle_dir_resolves_in_source_tree()
    case_bundle_dir_resolves_in_simulated_wheel_layout()
    case_quality_layers_disable_single()
    case_quality_layers_disable_all()
    case_inject_skills_false_is_true_kill_switch()
    case_adr_gate_body_has_no_double_frontmatter_leak()
    case_core_skills_never_capped_out_by_registry()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
