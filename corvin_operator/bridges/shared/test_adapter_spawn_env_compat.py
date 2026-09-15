"""Per-subtask E2E for Phase-7 spawn-env contract in adapter._build_spawn_env.

Phase 7 (v1.0) removes the CORVIN_* legacy aliases from the spawn env.
Only the canonical CORVIN_* names are written.

Contract (Phase 7):
  - CORVIN_CHANNEL_ID always set; CORVIN_CHANNEL_ID absent.
  - chat_key sanitized (slashes -> '_'); CORVIN name sees the sanitized form.
  - With a profile carrying a persona, CORVIN_CALLER_PERSONA is set;
    CORVIN_CALLER_PERSONA is absent.
  - Without a persona on the profile, CORVIN_CALLER_PERSONA is stripped
    from the env (defensive against parent-process leakage).
  - Base env passes through; only the CHANNEL_ID / CALLER_PERSONA keys
    are touched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

import adapter  # noqa: E402

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def test_channel_id_corvin_only() -> None:
    print("\n[CHANNEL_ID written under CORVIN_* only — CORVIN_* absent]")
    env = adapter._build_spawn_env(bridge="discord", chat_key="42",
                                   base={"PATH": "/usr/bin"})
    t("CORVIN_CHANNEL_ID present", "CORVIN_CHANNEL_ID" in env)
    t("CORVIN_CHANNEL_ID == discord:42",
      env.get("CORVIN_CHANNEL_ID") == "discord:42")
    t("base env preserved", env.get("PATH") == "/usr/bin")


def test_chat_key_sanitization() -> None:
    print("\n[chat_key sanitization applied to CORVIN_CHANNEL_ID]")
    env = adapter._build_spawn_env(bridge="whatsapp",
                                   chat_key="49/170/12345", base={})
    can = env.get("CORVIN_CHANNEL_ID", "")
    t("CORVIN value sanitized (no /)",
      "/" not in can.split(":", 1)[1], detail=f"got {can!r}")


def test_persona_corvin_only() -> None:
    print("\n[CALLER_PERSONA written under CORVIN_* only — CORVIN_* absent]")
    env = adapter._build_spawn_env(
        bridge="discord", chat_key="abc",
        base={}, profile={"persona": "research"},
    )
    t("CORVIN_CALLER_PERSONA == research",
      env.get("CORVIN_CALLER_PERSONA") == "research")


def test_persona_lookup_order() -> None:
    print("\n[persona lookup: _auto_routed > persona > _persona]")
    env = adapter._build_spawn_env(
        bridge="x", chat_key="1", base={},
        profile={"_auto_routed": "auto", "persona": "pin", "_persona": "diag"},
    )
    t("auto-routed wins over pin", env.get("CORVIN_CALLER_PERSONA") == "auto")
    env = adapter._build_spawn_env(
        bridge="x", chat_key="1", base={},
        profile={"persona": "pin", "_persona": "diag"},
    )
    t("pin beats _persona", env.get("CORVIN_CALLER_PERSONA") == "pin")
    env = adapter._build_spawn_env(
        bridge="x", chat_key="1", base={},
        profile={"_persona": "diag"},
    )
    t("_persona used as last resort",
      env.get("CORVIN_CALLER_PERSONA") == "diag")


def test_persona_strip_when_profile_truthy_but_persona_missing() -> None:
    print("\n[truthy profile w/o persona → CORVIN name stripped from base env]")
    base = {
        "PATH": "/usr/bin",
        "CORVIN_CALLER_PERSONA": "stale_a",
    }
    env = adapter._build_spawn_env(bridge="x", chat_key="1",
                                   base=base,
                                   profile={"some_other_field": "x"})
    t("CORVIN_CALLER_PERSONA stripped",
      "CORVIN_CALLER_PERSONA" not in env)
    t("PATH preserved", env.get("PATH") == "/usr/bin")


def test_persona_strip_when_blank_string() -> None:
    print("\n[blank persona string strips CORVIN name]")
    base = {"CORVIN_CALLER_PERSONA": "stale"}
    env = adapter._build_spawn_env(bridge="x", chat_key="1",
                                   base=base, profile={"persona": "   "})
    t("CORVIN stripped on whitespace persona",
      "CORVIN_CALLER_PERSONA" not in env)


def test_no_profile_means_no_persona_keys() -> None:
    print("\n[profile=None → no persona names in env]")
    env = adapter._build_spawn_env(bridge="x", chat_key="1", base={})
    t("CORVIN_CALLER_PERSONA absent",
      "CORVIN_CALLER_PERSONA" not in env)
    t("CORVIN_CALLER_PERSONA absent (Phase 7)",
      "CORVIN_CALLER_PERSONA" not in env)
    t("CORVIN_CHANNEL_ID still set", "CORVIN_CHANNEL_ID" in env)


def main() -> int:
    test_channel_id_corvin_only()
    test_chat_key_sanitization()
    test_persona_corvin_only()
    test_persona_lookup_order()
    test_persona_strip_when_profile_truthy_but_persona_missing()
    test_persona_strip_when_blank_string()
    test_no_profile_means_no_persona_keys()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
