#!/usr/bin/env python3
"""E2E tests for explicit-user-skill-request honoring in skill_inject.

Reproduces the proven defect from session web:9gCJXQnmhy: a user names a
skill that EXISTS on disk but is UNGRADED, so the AUTO injection grade-gate
(`n_grades < 1 or mean_score <= 0`) silently excludes it. The fix honors an
explicit request regardless of grade, respects the persona namespace-gate,
and diagnoses every non-injection LOUDLY (content-free WARNING + counter).

Drives the real skill-forge MultiSkillRegistry against a tempdir CORVIN_HOME.

Run as: python3 operator/bridges/shared/test_skill_inject_explicit_request.py
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
_TD = Path(tempfile.mkdtemp(prefix="explicit-skill-inject-"))
os.environ["CORVIN_HOME"] = str(_TD)
os.environ["CORVIN_FORCE_SCOPE"] = "user"
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(_TD / "slot")
os.environ.pop("LDD_AUTO_OPTIN", None)

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


# Prose-heavy body so the SkillForge linter's code-density check passes.
_BODY = (
    "This skill guides the design of an admin panel layout. It describes how "
    "to place navigation, primary actions, and status widgets so the panel "
    "reads as one coherent system. The body is intentionally prose-heavy so "
    "the linter's code-density check does not trip on it.\n\n"
    "Second paragraph: more prose, more sentences, more good advice about "
    "spacing, hierarchy, and consistent affordances across the panel.\n"
)


def _make_skills(reg: MultiSkillRegistry) -> None:
    # UNGRADED skill in the assistant namespace — the exact forensic shape.
    reg.create(name="assistant.test_panel_design", type="domain",
               body_md=_BODY, description="panel design (ungraded)")
    # A SECOND ungraded assistant skill, never requested — must stay excluded
    # from the AUTO path (grade gate intact).
    reg.create(name="assistant.other_ungraded", type="domain",
               body_md=_BODY, description="other ungraded skill")
    # A skill OUTSIDE the assistant namespace — an assistant persona must not
    # be able to inject it even when it explicitly names it.
    reg.create(name="code.forbidden_panel", type="domain",
               body_md=_BODY, description="code-namespace skill")
    # NB: no grades written anywhere — every skill above is ungraded.


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


def _reset_diag() -> None:
    skill_inject._request_diag_seen.clear()
    skill_inject._request_diag_counts.clear()


# ── Test cases ─────────────────────────────────────────────────────────────


def case_ungraded_explicit_now_injected():
    print("\n[1] explicit request → ungraded skill IS injected (the fix)")
    _reset_diag()
    block = skill_inject.collect_active_skills(
        channel_id="bridge:t1", profile=None,
        task_text="nutze den skill assistant.test_panel_design und baue ein panel",
        persona="assistant",
    )
    names = _block_names(block)
    t("assistant.test_panel_design injected despite being ungraded",
      "assistant.test_panel_design" in names, detail=f"names={names}")


def case_auto_gate_intact():
    print("\n[2] NON-requested ungraded skill is still NOT auto-injected")
    _reset_diag()
    # Request only the panel skill; the other ungraded skill must not ride along.
    block = skill_inject.collect_active_skills(
        channel_id="bridge:t2", profile=None,
        task_text="nutze den skill assistant.test_panel_design",
        persona="assistant",
    )
    names = _block_names(block)
    t("assistant.other_ungraded NOT injected (grade gate intact)",
      "assistant.other_ungraded" not in names, detail=f"names={names}")
    # And with NO explicit request, the panel skill itself stays excluded.
    _reset_diag()
    block2 = skill_inject.collect_active_skills(
        channel_id="bridge:t2b", profile=None,
        task_text="please build me a nice dashboard",
        persona="assistant",
    )
    names2 = _block_names(block2)
    t("assistant.test_panel_design NOT auto-injected without a request",
      "assistant.test_panel_design" not in names2, detail=f"names={names2}")


def case_loud_diagnostic_wrong_namespace():
    print("\n[3+4] cross-namespace explicit request refused + diagnosed LOUDLY")
    _reset_diag()
    block = skill_inject.collect_active_skills(
        channel_id="bridge:t3", profile=None,
        task_text="use skill code.forbidden_panel for this",
        persona="assistant",
    )
    names = _block_names(block)
    t("code.forbidden_panel NOT injected across the namespace gate",
      "code.forbidden_panel" not in names, detail=f"names={names}")
    t("loud diagnostic fired with reason=wrong_namespace",
      skill_inject._request_diag_counts.get("wrong_namespace", 0) >= 1,
      detail=f"counts={dict(skill_inject._request_diag_counts)}")


def case_loud_diagnostic_not_found():
    print("\n[5] explicit request for a non-existent skill diagnosed LOUDLY")
    _reset_diag()
    block = skill_inject.collect_active_skills(
        channel_id="bridge:t4", profile=None,
        task_text="use skill assistant.does_not_exist_anywhere",
        persona="assistant",
    )
    names = _block_names(block)
    t("assistant.does_not_exist_anywhere NOT injected",
      "assistant.does_not_exist_anywhere" not in names)
    t("loud diagnostic fired with reason=not_found",
      skill_inject._request_diag_counts.get("not_found", 0) >= 1,
      detail=f"counts={dict(skill_inject._request_diag_counts)}")


def case_failsafe_no_crash_on_bad_input():
    print("\n[6] fail-safe: odd task_text never raises")
    _reset_diag()
    try:
        skill_inject.collect_active_skills(
            channel_id="bridge:t5", profile=None,
            task_text="e.g. see config.py and adapter.py — no skills here",
            persona="assistant",
        )
        # dotted prose (file names / e.g.) must NOT be mistaken for requests.
        t("file-name / e.g. prose produced no not_found diagnostics",
          skill_inject._request_diag_counts.get("not_found", 0) == 0,
          detail=f"counts={dict(skill_inject._request_diag_counts)}")
    except Exception as e:  # noqa: BLE001
        t("collect_active_skills did not raise", False, detail=repr(e))


def main():
    reg = MultiSkillRegistry(channel_id=None, project_root=None)
    _make_skills(reg)
    case_ungraded_explicit_now_injected()
    case_auto_gate_intact()
    case_loud_diagnostic_wrong_namespace()
    case_loud_diagnostic_not_found()
    case_failsafe_no_crash_on_bad_input()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
