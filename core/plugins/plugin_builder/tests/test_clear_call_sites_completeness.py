"""Mechanical completeness check for the ADR-0262/0263 review round 5 fix:
every ``session_store.clear()`` / ``ideation.clear()`` call site that has a
specific session object in hand must pass ``expected=``, or a future call
site can silently reintroduce the exact bug class rounds 1-4 each found a
new instance of — and nothing short of reading every call site by hand
would catch it (ADR-0262/0263 review round 6, Quality finding: "the safe
line is now the easy line, but nothing stops a 7th call site from skipping
it").

This test parses the real source of every file known to call ``clear()``
on either store and asserts, via AST (not a fragile regex), that every such
call either passes ``expected=`` or is in :data:`ALLOWED_UNCONDITIONAL` —
a small, explicit, reasoned exception list (today: the two "feature flag
was disabled mid-turn, drop whatever is there" cleanup paths, which have no
specific session object to protect). Adding a new unconditional call site
means editing this allowlist by hand, in the same commit, with a reason —
exactly the friction the missing enforcement round 6 flagged was absent.

**Known scope limits (ADR-0262/0263 review round 7, Backend finding 2) —
named explicitly rather than claimed as airtight:** :func:`_find_store_clear_calls`
matches only a direct ``<name>.clear(...)`` where ``<name>`` is a bare
identifier in :data:`_STORE_OBJECT_NAMES`. It does NOT resolve
``import ... as`` aliases, calls through a variable holding the bound
method (``fn = session_store.clear; fn(...)``), or ``getattr(store,
"clear")(...)`` — each of those is real Python and would slip past this
check with zero ``expected=``. It also only scans the four files in
:data:`_FILES_TO_CHECK`; a fifth production call site in a new file would
never be scanned at all. None of these evasions exist in the codebase
today (verified by round 7's Backend reviewer against synthetic probes),
so this stays a deliberate, documented scope cut rather than a silent gap
— tighten it (import/alias resolution, a glob over
``core/plugins/plugin_builder/`` plus the two known consumer files) if a
future call site actually needs one of these forms.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: Object names whose ``.clear(...)`` calls this check cares about — a
#: dict.clear() or set.clear() elsewhere in these files is not in scope.
_STORE_OBJECT_NAMES = {"session_store", "_pb_store", "ideation", "_ideation", "_pb_ideation"}

#: (file relative to repo root, function name the call is inside) pairs
#: that are DELIBERATELY unconditional — no specific session object is
#: being protected, because the caller is dropping "whatever is there" as
#: part of a feature-flag-disabled cleanup, not finishing/cancelling one
#: particular session. Keep this list SMALL — every entry needs the same
#: justification the module docstring above requires.
ALLOWED_UNCONDITIONAL = {
    ("core/console/corvin_console/slash_commands.py", "_plugin_builder_continue"),
    ("operator/bridges/shared/adapter.py", "_plugin_builder_bridge_reply"),
}

_FILES_TO_CHECK = (
    "core/plugins/plugin_builder/ideation.py",
    "core/plugins/plugin_builder/turn.py",
    "core/console/corvin_console/slash_commands.py",
    "operator/bridges/shared/adapter.py",
)


def _enclosing_function_name(tree: ast.AST, target: ast.Call) -> str | None:
    """The innermost ``def``/``async def`` that contains ``target``."""
    best: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if target.lineno >= node.lineno and (
                not hasattr(node, "end_lineno")
                or node.end_lineno is None
                or target.lineno <= node.end_lineno
            ):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best.name if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _find_store_clear_calls(source: str) -> list[tuple[ast.Call, str | None]]:
    """Every ``<store_object>.clear(...)`` call in ``source``, paired with
    its enclosing function name (or ``None`` at module level)."""
    tree = ast.parse(source)
    found: list[tuple[ast.Call, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "clear"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id in _STORE_OBJECT_NAMES):
            continue
        found.append((node, _enclosing_function_name(tree, node)))
    return found


def _has_expected_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "expected" for kw in call.keywords)


def test_every_store_clear_call_passes_expected_or_is_explicitly_allowlisted():
    violations: list[str] = []
    seen_allowlist_entries: set[tuple[str, str | None]] = set()

    for rel_path in _FILES_TO_CHECK:
        path = _REPO_ROOT / rel_path
        source = path.read_text(encoding="utf-8")
        for call, func_name in _find_store_clear_calls(source):
            if _has_expected_kwarg(call):
                continue
            key = (rel_path, func_name)
            if key in ALLOWED_UNCONDITIONAL:
                seen_allowlist_entries.add(key)
                continue
            violations.append(
                f"{rel_path}:{call.lineno} — clear() call in "
                f"{func_name or '<module level>'}() has no expected= and "
                f"is not in ALLOWED_UNCONDITIONAL. If this is a genuine "
                f"unconditional cleanup (no specific session in hand), add "
                f"it to the allowlist with a reason. Otherwise pass "
                f"expected=<your session object>."
            )

    assert not violations, "\n".join(violations)

    # Catch drift the other direction too: an allowlist entry that no
    # longer corresponds to any real unconditional call (the code was
    # fixed, or moved, or removed) is stale and should be deleted so the
    # allowlist doesn't silently widen over time.
    stale = ALLOWED_UNCONDITIONAL - seen_allowlist_entries
    assert not stale, (
        f"ALLOWED_UNCONDITIONAL has stale entries no longer matching any "
        f"unconditional clear() call — remove them: {stale}"
    )
