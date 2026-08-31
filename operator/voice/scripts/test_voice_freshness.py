#!/usr/bin/env python3
"""Regression-Tests für den „Voice spielt altes/falsches ab"-Bug.

Drei Symptome derselben Ursache:
  1. Stop-Hook läuft, NEUER User-Prompt liegt schon im Transcript →
     extract_last_user.py griff früher den NEUEN Prompt → falsche TASK.
  2. Stop-Hook läuft, NEUER Assistant-Text wurde noch nicht geflusht oder
     Hook lief verspätet → extract_last_assistant.py liefert ALTEN Text,
     kombiniert mit NEUER User-Frage → User hört „Zu deiner Frage X: alte
     Antwort Y".
  3. Generelle Race: Hook für Turn N läuft so spät, dass Turn N+1 schon
     User-Antwort hat → wir würden alten Inhalt vorlesen.

Run: python3 operator/voice/scripts/test_voice_freshness.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from extract_last_user import extract as extract_user  # noqa: E402
from extract_last_assistant import extract as extract_assistant  # noqa: E402
from transcript_is_stale import is_stale  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def write(events: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for evt in events:
        f.write(json.dumps(evt) + "\n")
    f.close()
    return Path(f.name)


def U(text: str) -> dict:
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": text}]}}


def A(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def A_tool(name: str = "Bash") -> dict:
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": name, "input": {}}]}}


def U_tool_result() -> dict:
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


# ── 1. extract_last_user — neue User-Msg nach Assistant darf NICHT gewinnen ─
print("\n=== Bug-Szenario: User tippt schon NEUE Frage, Hook lagging ===")
p = write([
    U("Erste Frage zum Thema"),
    A_tool(),
    U_tool_result(),
    A("Hier die alte Antwort auf erste Frage"),
    U("Komplett neue Frage, hat mit alter Antwort nichts zu tun"),
])
got_user = extract_user(p)
expect(got_user == "Erste Frage zum Thema",
       "extract_last_user gibt User-Prompt VOR letztem Assistant zurück",
       f"got: {got_user!r}")
got_asst = extract_assistant(p)
expect(got_asst == "Hier die alte Antwort auf erste Frage",
       "extract_last_assistant gibt letzte Assistant-Text-Antwort",
       f"got: {got_asst!r}")
expect(is_stale(p) is True,
       "is_stale erkennt: User-Msg liegt nach Assistant-Antwort")
p.unlink()

# ── 2. Normalfall: User → Assistant → kein neuer User → fresh ───────────────
print("\n=== Normalfall: kein Race ===")
p = write([
    U("Was ist 2+2?"),
    A("4"),
])
expect(extract_user(p) == "Was ist 2+2?",
       "User-Prompt korrekt extrahiert")
expect(extract_assistant(p) == "4",
       "Assistant-Antwort korrekt extrahiert")
expect(is_stale(p) is False,
       "is_stale=false bei Normalfall")
p.unlink()

# ── 3. Mehrere Hin-und-Her, alle gepaart ────────────────────────────────────
print("\n=== Mehrere Turns: extract liefert immer das letzte gepaarte ===")
p = write([
    U("Frage 1"),
    A("Antwort 1"),
    U("Frage 2"),
    A("Antwort 2"),
    U("Frage 3"),
    A("Antwort 3"),
])
expect(extract_user(p) == "Frage 3",
       "letzte Frage")
expect(extract_assistant(p) == "Antwort 3",
       "letzte Antwort")
expect(is_stale(p) is False,
       "is_stale=false (letzte Sequenz vollständig)")
p.unlink()

# ── 4. tool_result-Wrapper-User dürfen NICHT als „echter" User zählen ───────
print("\n=== tool_result-User wird NICHT als Frage interpretiert ===")
p = write([
    U("Echte Frage"),
    A_tool(),
    U_tool_result(),  # technisch "user"-Eintrag, aber nur tool_result
    A("Antwort"),
    A_tool(),
    U_tool_result(),  # noch ein tool_result
])
expect(extract_user(p) == "Echte Frage",
       "extract_user überspringt tool_result-Wrapper",
       f"got: {extract_user(p)!r}")
expect(is_stale(p) is False,
       "is_stale=false (kein echter neuer User-Prompt)")
p.unlink()

# ── 5. Empty / kaputt: graceful degradation ─────────────────────────────────
print("\n=== Edge cases ===")
p = write([])
expect(extract_user(p) == "" and extract_assistant(p) == "" and not is_stale(p),
       "leeres Transcript → leer + nicht stale")
p.unlink()

# ── 6. Stale-Erkennung mit tool_result dazwischen ──────────────────────────
print("\n=== Stale auch wenn tool_result dazwischen liegt ===")
p = write([
    U("Erste Frage"),
    A("Erste Antwort"),
    U_tool_result(),         # not a real user msg
    U("Neue Frage nach Tool"),  # real user msg → stale
])
expect(extract_user(p) == "Erste Frage",
       "User-Prompt vor Assistant",
       f"got: {extract_user(p)!r}")
expect(is_stale(p) is True,
       "is_stale=true (echter User nach Assistant)")
p.unlink()

if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    exit_code = 1
else:
    print("\nALL CHECKS PASSED.")
    exit_code = 0

if __name__ == "__main__":
    sys.exit(exit_code)
