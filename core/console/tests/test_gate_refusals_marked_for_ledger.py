"""Every console path that persists a GATE REFUSAL marks it (ADR-2102).

The session ledger re-supplies earlier turns into the system prompt; a turn a
pre-spawn gate refused must be withheld, or the refused text reaches the
model past the gate. The marker is ``gate_refused=`` on ``_append_turn``. This
guard fails on the next refusal path that forgets it (review R3: the fallback-
engine refusal at the time did).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "corvin_console" / "chat_runtime.py"


def _text_names(call: ast.Call) -> set[str]:
    return {n.id for a in call.args for n in ast.walk(a) if isinstance(n, ast.Name)}


def test_every_persisted_gate_refusal_carries_the_marker():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    refusal_vars = re.compile(r"(gate|refus)", re.IGNORECASE)
    offenders, seen = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_append_turn":
            if any(refusal_vars.search(n) for n in _text_names(node)):
                seen += 1
                if not any(k.arg == "gate_refused" for k in node.keywords):
                    offenders.append(node.lineno)
    assert seen >= 2, "positive control: the known refusal paths were not found"
    assert not offenders, f"gate refusal persisted without gate_refused= at lines {offenders}"
