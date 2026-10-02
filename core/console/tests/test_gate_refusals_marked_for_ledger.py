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


def test_every_refusal_is_persisted_before_the_first_yield():
    """A disconnect closes the generator at a ``yield``; a refusal persisted
    after one is lost and its user line re-supplied (review R4-1). Inside the
    ``if <refusal> is not None:`` block, the marked append precedes every yield.
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    checked, offenders = 0, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and re.search(r"gate|refus", node.test.left.id, re.IGNORECASE)):
            continue
        appends = [n.lineno for st in node.body for n in ast.walk(st)
                   if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_append_turn"
                   and any(k.arg == "gate_refused" for k in n.keywords)]
        yields = [n.lineno for st in node.body for n in ast.walk(st)
                  if isinstance(n, (ast.Yield, ast.YieldFrom))]
        if not appends or not yields:
            continue
        checked += 1
        if min(appends) > min(yields):
            offenders.append(node.lineno)
    assert checked >= 2, "positive control: the known refusal blocks were not found"
    assert not offenders, f"refusal persisted after a yield in blocks at lines {offenders}"
