"""ADR-0171 — the load-bearing invariant: NO ENGINE RUNS WITHOUT A SPAN.

This is the AST guard the ADR promised. Instead of a hand-maintained registry
(which silently rots — a new spawn site added in a new file would never be
noticed), this test SCANS the production source tree with the `ast` module for
every real engine-spawn CALL site — `engine.spawn(...)` / `worker.spawn(...)` /
`create_subprocess_exec(...)` — and asserts the containing file ALSO emits an
engine.span. AST sees Calls only, so docstrings/comments mentioning
``engine.spawn(...)`` and ``def spawn(...)`` protocol definitions are correctly
ignored. A new spawn site with no span fails CI — making "every engine fully
auditable" structurally true, not hoped-for.

A file that legitimately spawns but is intentionally not span-wrapped must be
added to EXEMPT with a reason (none today).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent
_REPO = _SHARED.parents[2]

# Production source roots that can spawn an engine.
_SCAN_DIRS = [
    _REPO / "operator" / "bridges" / "shared",
    _REPO / "core" / "console" / "corvin_console",
    _REPO / "core" / "gateway" / "corvin_gateway",
    _REPO / "core" / "delegate" / "corvin_delegate",
]

# Files that contain a spawn call but are intentionally NOT span-wrapped.
# Map: relative-path-suffix -> reason. Empty today (every spawn site is wrapped).
EXEMPT: dict[str, str] = {}

# Any of these substrings in the file proves it emits an engine.span.
_SPAN_MARKERS = ("engine_span", "ENGINE_SPAN", "engine.span")


def _is_engine_spawn_call(node: ast.AST) -> bool:
    """True if `node` is a real engine-spawn Call: ANY `<recv>.spawn(...)` (engine,
    worker, _cop_eng, self.engine, factory()…), or (asyncio.)create_subprocess_exec.

    Matches every receiver — NOT just `engine`/`worker` — so a new spawn idiom in a
    new file cannot evade the guard (review finding: the narrow form missed
    `_cop_eng.spawn`). `def spawn` protocol definitions are FunctionDefs and
    docstring mentions are Strings, so neither is an ast.Call → no false positives."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        if fn.attr in ("spawn", "create_subprocess_exec"):
            return True
    if isinstance(fn, ast.Name) and fn.id == "create_subprocess_exec":
        return True
    return False


def _iter_prod_files():
    for d in _SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            name = p.name
            if name.startswith("test_") or name == "conftest.py":
                continue
            # Path-component checks (NOT slash substrings) so the exclusion holds
            # on Windows too (platform-independence constraint).
            if "tests" in p.parts or "__pycache__" in p.parts:
                continue
            yield p


def _spawn_files() -> list[Path]:
    out = []
    for p in _iter_prod_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        if any(_is_engine_spawn_call(n) for n in ast.walk(tree)):
            out.append(p)
    return out


def test_every_spawn_site_emits_a_span():
    """AST scan: each file with a real engine-spawn call must emit an engine.span
    (or be on EXEMPT). This catches a NEW spawn site in a NEW file — the failure
    mode a hard-coded registry cannot."""
    offenders = []
    for p in _spawn_files():
        rel = str(p.relative_to(_REPO))
        if any(rel.endswith(k) for k in EXEMPT):
            continue
        src = p.read_text(encoding="utf-8")
        if not any(m in src for m in _SPAN_MARKERS):
            offenders.append(rel)
    assert not offenders, (
        "engine-spawn sites with NO engine.span emission (ADR-0171 invariant "
        f"'no engine runs without a span'): {offenders}. Span-wrap them or add "
        "to EXEMPT with a reason."
    )


def test_scan_finds_the_known_spawn_sites():
    """Guard the guard: the scan must actually be finding spawn sites (a scan that
    silently matches nothing would vacuously pass). All known sites present."""
    found = {p.name for p in _spawn_files()}
    expected = {
        "adapter.py",                                  # bridge OS turn
        "a2a_worker.py", "awp_walker.py",             # shared workers
        "task_worker_pool.py",                         # console task daemon
        "dispatcher.py",                               # gateway run API
        "delegation.py",                               # delegate MCP tools
        "chat_runtime.py",                             # console OS turn
    }
    missing = expected - found
    assert not missing, f"scan no longer sees known spawn sites: {missing}"


def test_os_paths_pair_span_on_disconnect():
    # ADR-0171 pairing invariant: every OS-turn cancellation handler must catch
    # GeneratorExit (consumer aclose() on client disconnect) — NOT just
    # CancelledError — so engine.span.start always gets a matching end (no orphan).
    src = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text(encoding="utf-8")
    combined = src.count("except (asyncio.CancelledError, GeneratorExit)")
    assert combined >= 3, (
        f"expected >=3 GeneratorExit-catching OS cancellation handlers "
        f"(claude/delegation/hermes), found {combined} — an orphan-span path remains"
    )


def test_acs_worker_and_manager_pair_on_cancel():
    # ADR-0171: the ACS worker spawn (run inside asyncio.to_thread) and the
    # manager loop must pair their spans on CancelledError, which bypasses
    # `except Exception`. Assert both explicit CancelledError handlers exist.
    src = (_SHARED / "acs_runtime.py").read_text(encoding="utf-8")
    assert src.count("except asyncio.CancelledError") >= 2, (
        "acs_runtime must pair worker + manager spans on CancelledError "
        "(>=2 explicit handlers)"
    )
    assert 'role="manager"' in src, "acs_runtime must emit a role=manager span"


def test_engine_span_module_importable_and_registers_allowlist():
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    import engine_span as ES  # type: ignore
    assert ES.ENGINE_SPAN_START == "engine.span.start"
    assert ES.ENGINE_SPAN_END == "engine.span.end"
    for f in ES.START_FIELDS | ES.END_FIELDS:
        assert f not in ("prompt", "output", "text", "user", "uid", "email")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
