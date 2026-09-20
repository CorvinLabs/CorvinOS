"""Every console-audit call site must match the emitter's real signature.

Why this exists
---------------
``console_audit.action_performed`` is keyword-only and takes NO ``details``
parameter — the payload it writes is curated field-by-field so that the L16
metadata-only rule can be enforced statically.  Nine call sites passed
``details=...`` (and two passed the action positionally) anyway.  Python only
notices at call time, so:

* ``GET /v1/console/api/v1/marketplace/plugins`` raised
  ``TypeError: action_performed() got an unexpected keyword argument 'rec'``
  and answered 500 — the marketplace panel never loaded;
* the five ACO self-healing emitters raised the same TypeError inside a
  best-effort ``try/except``, so those audit records silently never existed.

A unit test of the emitter cannot catch this — the emitter was always correct.
The defect lives in the callers, so the fence has to scan the callers.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from corvin_console import audit as console_audit

_REPO = Path(__file__).resolve().parents[3]
_ROOTS = ("core", "corvin_gateway", "corvin_operator")
_PRUNE = {".venv", "node_modules", ".git", "dist", "dist.next", "dist.prev",
          "__pycache__", "site-packages", "build"}

#: Emitters whose call sites this fence checks. Every one is keyword-only.
_CHECKED = ("action_performed", "action_denied", "action_failed",
            "session_started", "session_ended", "session_denied")


def _signature_kwargs(name: str) -> set[str]:
    sig = inspect.signature(getattr(console_audit, name))
    return {
        p.name for p in sig.parameters.values()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in _ROOTS:
        base = _REPO / root
        if not base.is_dir():
            continue
        stack = [base]
        while stack:
            d = stack.pop()
            for entry in d.iterdir():
                if entry.is_dir():
                    if entry.name not in _PRUNE:
                        stack.append(entry)
                elif entry.suffix == ".py":
                    out.append(entry)
    return out


def _bad_call_sites() -> list[tuple[str, int, str]]:
    allowed = {name: _signature_kwargs(name) for name in _CHECKED}
    bad: list[tuple[str, int, str]] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in allowed:
                continue
            rel = str(path.relative_to(_REPO))
            if node.args:
                bad.append((rel, node.lineno,
                            f"{func.attr}() takes keyword arguments only, "
                            f"got {len(node.args)} positional"))
            extra = {kw.arg for kw in node.keywords if kw.arg} - allowed[func.attr]
            if extra:
                bad.append((rel, node.lineno,
                            f"{func.attr}() has no parameter(s) {sorted(extra)}; "
                            f"accepts {sorted(allowed[func.attr])}"))
    return bad


def test_the_scan_has_reach() -> None:
    """Positive control: a zero-findings sweep is only meaningful if the sweep
    actually looked at the file the historical defect lived in."""
    files = {str(p.relative_to(_REPO)) for p in _python_files()}
    assert "core/console/corvin_console/routes/marketplace.py" in files
    assert "core/console/corvin_core/aco/boot_healer.py" in files
    assert len(files) > 500, f"suspiciously few files scanned: {len(files)}"


def test_no_call_site_passes_an_argument_the_emitter_has_no_parameter_for() -> None:
    bad = _bad_call_sites()
    assert not bad, "console-audit call sites that will TypeError at runtime:\n" + "\n".join(
        f"  {f}:{line} — {why}" for f, line, why in bad
    )


def test_action_performed_still_rejects_details() -> None:
    """Guards the fence itself: if ``details`` is ever added as a parameter the
    scan above would go quiet, so this pins the premise it rests on."""
    assert "details" not in _signature_kwargs("action_performed")


@pytest.mark.parametrize("event", [
    "marketplace.discover",
    "marketplace.slo_check",
    "marketplace.skills.discover",
    "marketplace.skills.search",
    "aco.nerve_signal",
    "aco.integrity_alert",
    "aco.integrity_scan",
    "aco.boot_heal",
    "aco.engine_heal",
])
def test_replacement_events_are_field_allowlisted(event: str) -> None:
    """The context those call sites used to pass as ``details`` now travels as
    its own system event — which is only audit-safe if the field allowlist
    knows it (otherwise the core writer drops the keys silently)."""
    assert event in console_audit._ALLOWED_FIELDS
