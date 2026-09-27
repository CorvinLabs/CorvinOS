"""Regression (2026-09-27 adversarial review, round 2): no free name in the
bridge modules may be undefined at module scope.

Each of these was a NameError swallowed by a surrounding ``except``:
``_os_model`` (delegation badge never rendered), ``logging`` (drain shutdown
never flushed logs), ``tenant_id`` in ``session_reset`` (dialectic heat probe
always 0) and ``data`` in ``acs_validator`` (validator raised on
``tool_creation``). ``symtable`` sees them without importing the module.
"""
from __future__ import annotations

import builtins
import symtable
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

_MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__spec__", "__builtins__",
                   "__loader__", "__package__", "__path__"}


def _undefined(path: Path) -> list[tuple[str, str]]:
    top = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    defined = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported() or s.is_namespace()}
    defined |= set(dir(builtins)) | _MODULE_DUNDERS
    bad: set[tuple[str, str]] = set()

    def walk(t):
        for s in t.get_symbols():
            if not s.is_referenced() or s.get_name() in defined:
                continue
            if t.get_type() == "class" and s.is_local():
                continue
            if s.is_global() or (t is top and not s.is_assigned() and not s.is_imported()):
                bad.add((s.get_name(), t.get_name()))
        for c in t.get_children():
            walk(c)

    walk(top)
    return sorted(bad)


@pytest.mark.parametrize("name", ["adapter.py", "session_reset.py", "acs_validator.py"])
def test_no_undefined_module_names(name):
    assert _undefined(HERE / name) == []
