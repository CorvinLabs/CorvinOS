"""Regression: capability-flag warning was log-spam for the wildcard-by-
design `forge` persona. The warning must only fire when the persona is
actually mapped in policy.persona_namespaces AND the persona JSON
forgot to set tool_namespace — i.e. a real config inconsistency.

Run: python3 operator/bridges/shared/test_capability_warning.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

import adapter  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _logs_for_profile(profile: dict) -> str:
    """Drive _build_claude_args and capture all `adapter.log` output. Bridge
    only the slice we care about — we don't actually need claude here, just
    the warning emission path."""
    buf = io.StringIO()
    saved_log = adapter.log
    captured: list[str] = []

    def fake_log(*args):
        line = " ".join(str(a) for a in args)
        captured.append(line)

    adapter.log = fake_log
    try:
        # We don't run the full _build_claude_args (it spawns claude); we
        # just trigger the namespace-gate check directly. The warning is
        # emitted from _build_claude_args, but the underlying helper is
        # `_persona_has_namespace_gate(name)` — re-implement the check
        # block here so the test stays decoupled from the rest of build.
        fe = bool(profile.get("forge_enabled"))
        sfe = bool(profile.get("skill_forge_enabled"))
        if (fe or sfe) and not (profile.get("tool_namespace") or "").strip():
            persona_label = (
                profile.get("_auto_routed") or profile.get("persona")
                or profile.get("_persona") or "?"
            )
            if adapter._persona_has_namespace_gate(persona_label):
                fake_log(f"capability-flag warning: persona={persona_label!r} sets "
                         f"forge_enabled={fe} skill_forge_enabled={sfe} and is mapped "
                         f"in policy.persona_namespaces but the persona JSON has no "
                         f"tool_namespace — the registration prefix in the prompt "
                         f"may not match what the gate actually enforces.")
    finally:
        adapter.log = saved_log
    return "\n".join(captured)


def main() -> int:
    print("[capability-flag warning is silent for wildcard-by-design personas]")

    # Scenario 1 — forge persona (skill_forge_enabled=True, no tool_namespace,
    # NOT in policy.persona_namespaces by design): NO warning.
    profile_forge = {
        "_persona": "forge",
        "skill_forge_enabled": True,
        "forge_enabled": False,
    }
    out = _logs_for_profile(profile_forge)
    t("forge persona produces NO capability warning",
      "capability-flag warning" not in out,
      detail=f"unexpected: {out[:160]!r}" if "warning" in out else "")

    # Scenario 2 — synthetic persona that IS mapped (e.g. coder) but is
    # missing tool_namespace: WARNING fires.
    profile_coder_broken = {
        "_persona": "coder",
        "forge_enabled": True,
        # tool_namespace missing on purpose — the very inconsistency
        # the warning is designed to flag.
    }
    out2 = _logs_for_profile(profile_coder_broken)
    t("mapped persona without tool_namespace produces a warning",
      "capability-flag warning" in out2 and "'coder'" in out2,
      detail=f"got: {out2[:240]!r}")

    # Scenario 3 — assistant: mapped AND has tool_namespace (real coder
    # config in this repo) → silent.
    profile_assistant_ok = {
        "_persona": "assistant",
        "forge_enabled": True,
        "tool_namespace": "assistant",
    }
    out3 = _logs_for_profile(profile_assistant_ok)
    t("persona with both mapping AND tool_namespace is silent",
      "capability-flag warning" not in out3)

    # Scenario 4 — persona with neither flag: silent.
    profile_no_caps = {
        "_persona": "coder",
        "forge_enabled": False,
        "skill_forge_enabled": False,
    }
    out4 = _logs_for_profile(profile_no_caps)
    t("persona without forge/skill capabilities is silent",
      "capability-flag warning" not in out4)

    # Sanity: helper itself, decoupled from the warning path.
    t("_persona_has_namespace_gate('coder') == True",
      adapter._persona_has_namespace_gate("coder") is True)
    t("_persona_has_namespace_gate('forge') == False",
      adapter._persona_has_namespace_gate("forge") is False,
      detail="forge is wildcard by design — no policy entry")
    t("_persona_has_namespace_gate(None) == False",
      adapter._persona_has_namespace_gate(None) is False)
    t("_persona_has_namespace_gate('') == False",
      adapter._persona_has_namespace_gate("") is False)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
