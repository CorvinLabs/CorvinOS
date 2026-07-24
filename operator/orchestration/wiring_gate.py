"""ADR-0215 Phase 1 — Wiring Manifest loader + Reachability Gate.

Two independent checks, both structural prevention for the bug class this
ADR exists to close (see ADR-0215 in Corvin-ADR/decisions/):

1. **Manifest completeness + reachability.** Every ``.py`` module under a
   registered orchestration directory must have exactly one entry in that
   directory's ``WIRING.yaml``, declared ``live`` (its ``entry_point`` must
   actually import and resolve) or ``deferred`` (an explicit, ADR-tracked
   "not wired yet, on purpose" declaration — never silent). A module with
   neither is a hard FAIL: this is what would have caught
   ``streaming_executor.py`` / ``detector_plugin_registry.py`` staying
   unwired through 4+ review rounds.

2. **Dotted ``operator.`` import lint.** ``operator/`` has no
   ``__init__.py`` and always loses to the stdlib ``operator`` module
   regardless of sys.path order — ``from operator.X.Y import Z`` /
   ``import operator.X.Y`` can NEVER resolve. This scans real import
   statements (not comments, not string literals) repo-wide for that
   pattern.

Dynamic-dispatch note: a ``live`` entry whose ``entry_point`` module imports
fine but whose actual production caller only reaches it via a dict lookup
(``EngineRegistry.engines[name]``) or ``importlib`` plugin loading cannot be
proven reachable by static analysis alone. This gate does NOT attempt full
call-graph soundness for that case (see ADR-0215 Non-Goals) — it only
verifies the entry_point *resolves*. Cross-checking that it is *actually
invoked* in production is ``WiringIntegrityFiber``'s job (ADR-0215 Phase 2),
which watches real ``tde.*`` audit-event traffic instead of static structure.
"""
from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_REPO_ROOT = Path(__file__).resolve().parents[2]

# (source directory, WIRING.yaml path, sys.path entry needed to import
#  modules named in that manifest, prefix stripped when discovering .py
#  files so the discovered name matches the manifest's `module:` field).
_MANIFESTS: tuple[tuple[Path, Path, Path], ...] = (
    (
        _REPO_ROOT / "operator" / "orchestration" / "tde",
        _REPO_ROOT / "operator" / "orchestration" / "tde" / "WIRING.yaml",
        _REPO_ROOT / "operator" / "orchestration",  # so `tde.X` imports work
    ),
    (
        _REPO_ROOT / "operator" / "orchestration",
        _REPO_ROOT / "operator" / "orchestration" / "WIRING.yaml",
        _REPO_ROOT / "operator" / "orchestration",
    ),
)

# Repo-wide dotted-import lint scope. Kept narrow (operator/, core/) rather
# than the whole tree — vendored/third-party code (node_modules, .venv) must
# never be scanned, and would produce meaningless noise if it were.
_LINT_SCAN_ROOTS: tuple[Path, ...] = (
    _REPO_ROOT / "operator",
    _REPO_ROOT / "core",
)
_LINT_EXCLUDE_DIR_NAMES = {
    "__pycache__", "node_modules", ".venv", "venv", "web-next", "dist",
    "build", ".git",
}


@dataclass
class Finding:
    severity: str  # "FAIL" | "WARN"
    check: str
    message: str
    path: Optional[str] = None


@dataclass
class GateResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "FAIL"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "WARN"]

    @property
    def ok(self) -> bool:
        return not self.failures


def _discover_py_modules(directory: Path) -> set[str]:
    """Bare module names for every non-test .py file directly in `directory`
    (not recursive — each orchestration package is flat by convention)."""
    names = set()
    for p in directory.glob("*.py"):
        if p.name.startswith("test_") or p.name.startswith("_test"):
            continue
        names.add(p.stem)
    return names


def load_manifest(manifest_path: Path) -> list[dict]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load WIRING.yaml manifests")
    if not manifest_path.is_file():
        return []
    data = yaml.safe_load(manifest_path.read_text()) or {}
    return data.get("components", [])


def check_manifest_completeness(result: GateResult) -> None:
    for src_dir, manifest_path, _ in _MANIFESTS:
        entries = load_manifest(manifest_path)
        declared = {e["name"] for e in entries}
        actual = _discover_py_modules(src_dir)
        undeclared = actual - declared
        for name in sorted(undeclared):
            result.findings.append(Finding(
                severity="FAIL",
                check="manifest_completeness",
                message=(
                    f"{src_dir / (name + '.py')} has no entry in "
                    f"{manifest_path.name} — every module must be declared "
                    f"`live` or `deferred`, never implicit."
                ),
                path=str(src_dir / (name + ".py")),
            ))
        stale = declared - actual
        for name in sorted(stale):
            result.findings.append(Finding(
                severity="WARN",
                check="manifest_completeness",
                message=(
                    f"{manifest_path.name} declares `{name}` but "
                    f"{src_dir / (name + '.py')} no longer exists — stale "
                    f"manifest entry, remove it."
                ),
                path=str(manifest_path),
            ))


def _resolve_entry_point(entry_point: str, sys_path_entry: Path) -> tuple[bool, str]:
    """Returns (resolved, detail). Adds sys_path_entry, imports the module
    part of `module:symbol` (or just `module`), resolves the symbol via
    getattr if present."""
    module_name, _, symbol = entry_point.partition(":")
    sp = str(sys_path_entry)
    added = sp not in sys.path
    if added:
        sys.path.insert(0, sp)
    try:
        mod = importlib.import_module(module_name)
        if symbol:
            if not hasattr(mod, symbol):
                return False, f"module `{module_name}` imported but has no attribute `{symbol}`"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 — we want to report ANY import failure
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if added and sp in sys.path:
            sys.path.remove(sp)


def check_live_entry_points(result: GateResult) -> None:
    for _src_dir, manifest_path, sys_path_entry in _MANIFESTS:
        for entry in load_manifest(manifest_path):
            if entry.get("status") != "live":
                continue
            entry_point = entry.get("entry_point")
            if not entry_point:
                result.findings.append(Finding(
                    severity="FAIL",
                    check="live_entry_point",
                    message=f"`{entry['name']}` is declared `live` but has no `entry_point`",
                    path=str(manifest_path),
                ))
                continue
            ok, detail = _resolve_entry_point(entry_point, sys_path_entry)
            if not ok:
                result.findings.append(Finding(
                    severity="FAIL",
                    check="live_entry_point",
                    message=(
                        f"`{entry['name']}` declared `live` with entry_point "
                        f"`{entry_point}` but it does not resolve: {detail}"
                    ),
                    path=str(manifest_path),
                ))


def check_deferred_has_reason(result: GateResult) -> None:
    for _src_dir, manifest_path, _ in _MANIFESTS:
        for entry in load_manifest(manifest_path):
            if entry.get("status") == "deferred" and not (entry.get("reason") or "").strip():
                result.findings.append(Finding(
                    severity="FAIL",
                    check="deferred_reason",
                    message=f"`{entry['name']}` is `deferred` but has no `reason` — deferred status must be explained, not just asserted",
                    path=str(manifest_path),
                ))


def _dotted_operator_import_lines(text: str) -> list[tuple[int, str]]:
    """AST-based scan for real `import operator.X` / `from operator.X import
    Y` statements — deliberately NOT a regex over raw text, so docstring
    usage examples and comments (which regularly quote import lines as
    documentation) can never produce a false positive. A regex-based first
    version of this lint flagged operator/license/{sob,capability,
    seal_loader}.py's module docstrings, which merely *show* the (broken)
    dotted form as a — since-corrected — usage example; ``ast`` only sees
    real statement nodes, which are immune to that whole class of mistake."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []  # not our concern here; a real syntax error fails elsewhere
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from operator import X` (bare, the real stdlib module) is
            # fine and deliberately excluded — only the dotted submodule
            # form (`from operator.X... import Y`) can never resolve.
            if (node.module or "").startswith("operator."):
                names = ", ".join(a.name for a in node.names)
                hits.append((node.lineno, f"from {node.module} import {names}"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("operator."):
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def lint_dotted_operator_imports(result: GateResult) -> None:
    for scan_root in _LINT_SCAN_ROOTS:
        if not scan_root.is_dir():
            continue
        for py_file in scan_root.rglob("*.py"):
            if any(part in _LINT_EXCLUDE_DIR_NAMES for part in py_file.parts):
                continue
            try:
                text = py_file.read_text(errors="replace")
            except OSError:
                continue
            try:
                display_path = py_file.relative_to(_REPO_ROOT)
            except ValueError:
                # scan_root outside the repo (e.g. a test fixture under
                # /tmp) — fall back to the absolute path rather than crash.
                display_path = py_file
            for lineno, snippet in _dotted_operator_import_lines(text):
                result.findings.append(Finding(
                    severity="FAIL",
                    check="dotted_operator_import",
                    message=(
                        f"{display_path}:{lineno}: dotted "
                        f"`operator.` import can never resolve (stdlib "
                        f"`operator` shadows the repo's operator/ "
                        f"directory) — use the repo-relative sys.path + "
                        f"bare-import pattern instead: {snippet!r}"
                    ),
                    path=f"{display_path}:{lineno}",
                ))


def check_all() -> GateResult:
    result = GateResult()
    check_manifest_completeness(result)
    check_deferred_has_reason(result)
    check_live_entry_points(result)
    lint_dotted_operator_imports(result)
    return result


def _main() -> int:
    result = check_all()
    for f in result.findings:
        print(f"[{f.severity}] ({f.check}) {f.message}")
    print(f"\n{len(result.failures)} failure(s), {len(result.warnings)} warning(s)")
    return 1 if not result.ok else 0


if __name__ == "__main__":
    raise SystemExit(_main())
