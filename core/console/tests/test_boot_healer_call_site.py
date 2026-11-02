"""Every shipped host starts the ACO boot healer, and its audit writers resolve (PLAN-0969 WP-1).

Two defects, both of the "the mechanism is fine, nothing reaches it" class:

* ``corvin_console.standalone:create_app`` (what ``corvinos-serve`` and ``install.ps1`` run)
  never started the healer; only ``corvin_gateway.app`` did. A console-only install, which is
  every Windows install, had no self-repair at all.
* After the move to ``corvin_core.aco`` the healer's ``from .. import audit`` /
  ``from .. import chat_runtime`` pointed at modules that do not exist in ``corvin_core``. The
  ``ImportError`` was swallowed, so no ``aco.boot_heal`` / ``aco.engine_heal`` /
  ``aco.integrity_scan`` record was ever written, on any host.

The AST checks below pin the wiring; ``tests/sandbox/test_boot_healer_hosts.py`` proves the
behaviour through a real ``uvicorn`` process.
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_HEALER = _REPO / "core" / "console" / "corvin_core" / "aco" / "boot_healer.py"
_HOSTS = {
    "console (corvinos-serve, install.sh, install.ps1)":
        _REPO / "core" / "console" / "corvin_console" / "standalone.py",
    "gateway (corvin-service)":
        _REPO / "core" / "gateway" / "corvin_gateway" / "app.py",
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.name for a in node.names)
    return names


def _called_attrs(tree: ast.AST) -> set[str]:
    return {n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


@pytest.mark.parametrize("host", sorted(_HOSTS))
def test_every_host_starts_and_cancels_the_healer(host: str) -> None:
    tree = _tree(_HOSTS[host])
    assert "start_boot_healer" in _imported_names(tree), f"{host} never starts the boot healer"
    assert "cancel" in _called_attrs(tree), f"{host} never cancels a background task on shutdown"


def test_healer_has_no_relative_import_of_a_missing_sibling() -> None:
    pkg = _HEALER.parent.parent  # corvin_core/
    missing = []
    for node in ast.walk(_tree(_HEALER)):
        if isinstance(node, ast.ImportFrom) and node.level == 2 and not node.module:
            for alias in node.names:
                if not ((pkg / f"{alias.name}.py").exists() or (pkg / alias.name).is_dir()):
                    missing.append(alias.name)
    assert not missing, f"corvin_core has no {missing}: these imports always fail"


def test_audit_writers_reach_the_console_audit_module(monkeypatch) -> None:
    sys.path[:0] = [str(_REPO / "core" / "console"), str(_REPO / "corvin_operator" / "forge")]
    from corvin_core.aco import boot_healer
    import corvin_console.audit as console_audit

    seen: list[str] = []
    monkeypatch.setattr(console_audit, "system_event", lambda **kw: seen.append(kw["event"]))
    boot_healer._write_audit(1, 0, 0)
    assert seen == ["aco.boot_heal"]


def test_second_start_in_the_same_loop_returns_the_running_task() -> None:
    sys.path[:0] = [str(_REPO / "core" / "console"), str(_REPO / "corvin_operator" / "forge")]
    from corvin_core.aco import boot_healer

    async def _run():
        first = boot_healer.start_boot_healer(boot_delay=3600)
        second = boot_healer.start_boot_healer(boot_delay=3600)
        try:
            return first is second
        finally:
            first.cancel()

    assert asyncio.run(_run())
