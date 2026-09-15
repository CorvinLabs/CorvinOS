"""AST-based static check for forged Python tool sources.

This is the **first** layer of defense — the policy's forbidden imports are
rejected before the tool is ever registered. The bubblewrap sandbox is the
**second** layer (e.g. it unshares the network namespace, so even a tool
that imports ``socket`` can't reach the wire). Static checks catch obvious
abuse early and produce friendlier error messages than a runtime crash;
they cannot stop dynamic-import tricks like ``__import__("socket")`` —
that's what the sandbox is for.

The check is Python-AST-only. Bash impls are skipped (no AST), and a
syntax error produces a single ``<unparseable>`` violation rather than
crashing the loader.
"""
from __future__ import annotations

import ast
from typing import Iterable


class StaticCheckError(ValueError):
    """Raised when impl source is rejected by the static check."""

    def __init__(self, message: str, *, violations: list[str]):
        super().__init__(message)
        self.violations = violations


def scan_imports(impl: str) -> tuple[set[str], bool]:
    """Return (root_module_names, parseable).

    ``parseable=False`` means the source did not parse as Python; in that
    case the returned set contains the literal token ``"<unparseable>"``.
    Same-file ``from .foo import bar`` (relative imports) is ignored —
    such imports cannot reach standard-library modules.
    """
    try:
        tree = ast.parse(impl)
    except SyntaxError:
        return {"<unparseable>"}, False

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # ``import a.b.c`` → root is "a"
                root = alias.name.split(".", 1)[0]
                if root:
                    roots.add(root)
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports (level > 0): they can only reach
            # within the same package, not arbitrary stdlib.
            if node.level and not node.module:
                continue
            if node.module:
                root = node.module.split(".", 1)[0]
                if root:
                    roots.add(root)
    return roots, True


def check_imports(
    impl: str,
    *,
    forbidden: Iterable[str],
    runtime: str = "python",
) -> list[str]:
    """Return the list of forbidden imports the source uses.

    Empty list = passes the check. ``runtime != "python"`` skips checks
    (bash sources don't have Python imports). A syntax error in a python
    source surfaces as a single ``"<unparseable>"`` entry, since we
    can't statically prove the absence of a forbidden import in code we
    can't parse.
    """
    if runtime != "python":
        return []
    forbidden_set = set(forbidden)
    if not forbidden_set:
        return []
    roots, parseable = scan_imports(impl)
    if not parseable:
        # Refuse rather than approve unknown code.
        return ["<unparseable>"]
    violations = sorted(roots & forbidden_set)
    return violations


def assert_imports_ok(
    impl: str,
    *,
    forbidden: Iterable[str],
    runtime: str = "python",
) -> None:
    """Convenience wrapper: raise if any forbidden imports are present."""
    bad = check_imports(impl, forbidden=forbidden, runtime=runtime)
    if bad:
        raise StaticCheckError(
            f"forbidden imports: {', '.join(bad)}",
            violations=bad,
        )
