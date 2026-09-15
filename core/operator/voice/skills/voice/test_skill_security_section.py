"""F1 E2E: voice/SKILL.md must document the runtime tool generation surface.

A future Claude reading this skill should learn at a glance:
  - that a forge persona exists, and what its restrictive defaults are
  - how the per-persona allowed_forged_tools allowlist works
  - where the audit log lives and how to verify it
  - what policy.json controls and that edits hot-reload

We don't grade prose; we just check that load-bearing terms / paths /
CLI invocations actually appear in the skill. Failure = the doc drifted
from the shipped code surface.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent / "SKILL.md"


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    print("\n[voice/SKILL.md security section]")
    body = SKILL.read_text()

    t("file exists", SKILL.exists())

    # Top-level section heading
    t("section heading present",
      "## Runtime tool generation (forge plugin)" in body)

    # Forge persona is named, defaults stated
    t("names the forge persona",
      "forge persona" in body.lower() or "persona forge" in body.lower())
    t("calls out NO Bash/Edit/Write defaults",
      "Bash" in body and "Edit" in body and "Write" in body)
    t("mentions bwrap / sandbox isolation",
      "bwrap" in body.lower() or "sandbox" in body.lower())
    t("explains opt-in via chat_profile or routing-anchor",
      ("chat_profiles" in body and "routing" in body.lower())
      or ("routing-anchor" in body)
      or ("\"forge\"" in body and "chat_profile" in body))

    # Per-persona allowlist subsection
    t("Per-persona allowlist subsection present",
      "### Per-persona allowlist" in body)
    t("names allowed_forged_tools field",
      "allowed_forged_tools" in body)
    t("documents fnmatch glob semantics (csv.* not csv_x)",
      "fnmatch" in body or
      ("csv.*" in body and ("csv_x" in body or "literal" in body.lower())))
    t("absence = no restriction documented",
      "no restriction" in body.lower() or "no restriction" in body)

    # Audit log subsection
    t("Audit log subsection present",
      "### Audit log" in body)
    t("audit path named (~/.config/corvin-voice/forge/audit.jsonl)",
      "~/.config/corvin-voice/forge/audit.jsonl" in body)
    t("VOICE_AUDIT_PATH env mentioned",
      "VOICE_AUDIT_PATH" in body)
    t("hash chain explained",
      "hash" in body.lower() and "chain" in body.lower())
    t("voice-audit verify CLI documented",
      "voice-audit verify" in body)
    t("voice-audit tail CLI documented",
      "voice-audit tail" in body)

    # Workflow policy subsection
    t("Workflow policy subsection present",
      "### Workflow policy" in body)
    t("policy.json path named",
      "~/.config/corvin-voice/forge/policy.json" in body)
    t("FORGE_ROOT env mentioned",
      "FORGE_ROOT" in body)
    t("policy fields named",
      all(field in body for field in
          ("forbidden_imports", "forbidden_tool_names",
           "max_budget", "rate_limit", "circuit_breaker"))
      and ("audit.hash_chain" in body or "hash_chain" in body))
    t("hot-reload is documented",
      "hot-reload" in body.lower() or "hot reload" in body.lower()
      or "without restart" in body.lower())

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
