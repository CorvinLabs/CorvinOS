#!/usr/bin/env python3
"""test_engine_binary_resolution_guard.py — sustainability guard for the
stripped-PATH → hermes-timeout bug class.

Background
----------
The adapter runs under systemd / ``bridge.sh`` with a *stripped* PATH that
lacks ``~/.local/bin`` (where Claude Code installs the CLI). A bare
``shutil.which("claude")`` then returns ``None`` **even when claude is
installed**. Used as a yes/no engine-availability gate, that false-negative has
bitten three distinct subsystems over time:

  1. L44 house-rules Tier-1 classifier (fixed commit 79de989)
  2. ADR-0159 M1 OS-engine auto-detect → silently downgraded the OS turn to
     hermes → Ollama timeout ("hermes connect error: timed out") although
     claude was the intended engine
  3. ``acs_runtime._claude_binary`` (same stripped-PATH false-negative)

The canonical cure is the hardened resolver
(``helper_model.resolve_claude_bin`` / ``agents.claude_code._resolve_claude_bin``:
``CORVIN_CLAUDE_BIN`` → PATH → known install locations). This file makes the
*class* extinct rather than patching sites one funeral at a time:

  * ``test_no_bare_which_claude_availability_gate`` — AST scan that FAILS if any
    production module reintroduces ``which("claude") is None`` /
    ``not which("claude")`` as an availability gate. Comment-immune (AST, not
    grep). Legitimate resolver calls (``found = shutil.which(name)``) are not
    gates and are not flagged.
  * ``test_acs_runtime_claude_binary_*`` — behavioural proof that
    ``_claude_binary`` resolves an off-PATH claude install and honours an
    explicit pin.

The adapter auto-detect itself is proven by
``test_adapter_engine_path.test_engine_autodetect_offpath_claude_resolves_to_claude_code``.
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from . import acs_runtime as _rt  # type: ignore
except ImportError:
    import acs_runtime as _rt  # type: ignore[no-redef]


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# 1. AST guard — no bare which("claude") used as an availability gate
# ---------------------------------------------------------------------------


def _is_which_claude_call(node: ast.AST) -> bool:
    """True for ``which("claude")`` / ``shutil.which("claude")`` calls."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = None
    if isinstance(fn, ast.Name):
        name = fn.id
    elif isinstance(fn, ast.Attribute):
        name = fn.attr
    if name != "which":
        return False
    if not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and first.value == "claude"


class _GateFinder(ast.NodeVisitor):
    """Flags the availability-gate anti-pattern:

      which("claude") is None / is not None        (Compare against None)
      not which("claude")                          (UnaryOp Not)
      if which("claude"):                          (bare truthiness branch)
    """

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        if _is_which_claude_call(node.left) and any(
            isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops
        ):
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if isinstance(node.op, ast.Not) and _is_which_claude_call(node.operand):
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # `if which("claude"):` — bare truthiness branch is also a gate.
        if _is_which_claude_call(node.test):
            self.hits.append(node.lineno)
        self.generic_visit(node)


def test_no_bare_which_claude_availability_gate() -> None:
    _section("AST guard — no bare which('claude') availability gate in production")
    targets: list[Path] = []
    for base in (ROOT, ROOT / "agents"):
        if base.is_dir():
            targets.extend(
                p for p in base.glob("*.py") if not p.name.startswith("test_")
            )
    assert targets, "no production modules discovered to scan"
    offenders: list[str] = []
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        finder = _GateFinder()
        finder.visit(tree)
        for lineno in finder.hits:
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "bare which('claude') availability-gate reintroduced (stripped-PATH bug "
        "class) — route engine availability through helper_model.resolve_claude_bin "
        f"instead. Offenders: {offenders}"
    )
    print(f"PASS: scanned {len(targets)} production modules — no gate anti-pattern")


# ---------------------------------------------------------------------------
# 2. acs_runtime._claude_binary survives a stripped PATH
# ---------------------------------------------------------------------------


def test_acs_runtime_claude_binary_survives_stripped_path() -> None:
    _section("acs_runtime._claude_binary — off-PATH claude resolves, not bare name")
    work = Path(tempfile.mkdtemp(prefix="acs-claudebin-"))
    empty_dir = work / "bin"           # on PATH, no claude
    empty_dir.mkdir()
    offpath = work / "offpath" / "claude"
    offpath.parent.mkdir(parents=True)
    offpath.write_text("#!/bin/sh\nexit 0\n")
    offpath.chmod(0o755)
    saved = {
        k: os.environ.get(k)
        for k in ("PATH", "CORVIN_CLAUDE_BIN", "CORVIN_CLAUDE_BIN_FALLBACKS")
    }
    try:
        os.environ["PATH"] = str(empty_dir)          # claude NOT on PATH
        os.environ.pop("CORVIN_CLAUDE_BIN", None)     # no explicit pin
        os.environ["CORVIN_CLAUDE_BIN_FALLBACKS"] = str(offpath)
        resolved = _rt._claude_binary()
        assert resolved == str(offpath), (
            f"acs_runtime fell back to bare name under stripped PATH: {resolved!r} "
            f"(expected off-PATH {offpath})"
        )
        assert resolved != "claude", "resolver returned the unspawnable bare name"
        print(f"PASS: acs_runtime resolved off-PATH claude: {resolved!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_acs_runtime_claude_binary_honours_explicit_pin() -> None:
    _section("acs_runtime._claude_binary — explicit CORVIN_CLAUDE_BIN pin wins")
    saved = os.environ.get("CORVIN_CLAUDE_BIN")
    try:
        os.environ["CORVIN_CLAUDE_BIN"] = "/opt/custom/claude"
        resolved = _rt._claude_binary()
        assert resolved == "/opt/custom/claude", (
            f"explicit pin not honoured: {resolved!r}"
        )
        print(f"PASS: explicit pin honoured: {resolved!r}")
    finally:
        if saved is None:
            os.environ.pop("CORVIN_CLAUDE_BIN", None)
        else:
            os.environ["CORVIN_CLAUDE_BIN"] = saved


def main() -> int:
    tests = [
        test_no_bare_which_claude_availability_gate,
        test_acs_runtime_claude_binary_survives_stripped_path,
        test_acs_runtime_claude_binary_honours_explicit_pin,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
