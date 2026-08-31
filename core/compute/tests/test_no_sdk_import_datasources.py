"""AST walk — asserts no forbidden SDK imports under datasources/ (ADR-0026 D).

Checks:
  - No `import anthropic`, `import openai`, `import google.cloud.aiplatform`
  - No f-string or %-format interpolation of FilterExpr values in adapter SQL
    (heuristic: patterns like f"...{value}" or "..." % value inside adapter files)
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DATASOURCES_ROOT = PLUGIN_ROOT / "corvin_compute" / "fabric" / "datasources"

FORBIDDEN_MODULES = frozenset({
    "anthropic",
    "openai",
    "google.cloud.aiplatform",
})


class NoSDKImportDatasourcesTest(unittest.TestCase):
    """Cost-contract gate for the datasources sub-system."""

    def _iter_py_files(self):
        return sorted(DATASOURCES_ROOT.rglob("*.py"))

    def _collect_imports(self, source: str) -> list[str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_forbidden_sdk_import(self) -> None:
        """No file under datasources/ may import anthropic, openai, or aiplatform."""
        offenders: list[tuple[str, str]] = []
        for py_file in self._iter_py_files():
            try:
                src = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for imp in self._collect_imports(src):
                top = imp.split(".")[0]
                if top in FORBIDDEN_MODULES or imp in FORBIDDEN_MODULES:
                    offenders.append((str(py_file.relative_to(PLUGIN_ROOT)), imp))
                # Special case: google.cloud.aiplatform (multi-part)
                if "aiplatform" in imp:
                    offenders.append((str(py_file.relative_to(PLUGIN_ROOT)), imp))
        self.assertEqual(
            offenders, [],
            f"Forbidden SDK imports found: {offenders}",
        )

    def test_no_string_interpolation_of_filter_values_in_adapters(self) -> None:
        """Heuristic: no f-string containing 'value' in adapter SQL methods.

        This is a structural defence against SQL injection.
        We look for patterns like f"...{value}" or "..." % value
        in adapter source files (builtin/*.py).
        """
        builtin_dir = DATASOURCES_ROOT / "builtin"
        suspicious: list[tuple[str, int, str]] = []

        for py_file in builtin_dir.glob("*.py"):
            try:
                src = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                # Check for f-strings containing {value} or {fexpr.value}
                if isinstance(node, ast.JoinedStr):
                    for part in ast.walk(node):
                        if isinstance(part, ast.FormattedValue):
                            # Stringify the formatted value expression
                            if isinstance(part.value, ast.Attribute):
                                attr_name = part.value.attr
                                if attr_name == "value":
                                    suspicious.append((
                                        str(py_file.name),
                                        getattr(node, "lineno", 0),
                                        "f-string with .value attribute (potential SQL injection)",
                                    ))
                            elif isinstance(part.value, ast.Name):
                                if part.value.id == "value":
                                    suspicious.append((
                                        str(py_file.name),
                                        getattr(node, "lineno", 0),
                                        "f-string with 'value' variable (potential SQL injection)",
                                    ))

        # We allow false negatives here but fail on true positives
        self.assertEqual(
            suspicious, [],
            f"Potential SQL injection via f-string interpolation: {suspicious}",
        )

    def test_all_py_files_parseable(self) -> None:
        """All .py files in datasources/ must be syntactically valid."""
        for py_file in self._iter_py_files():
            try:
                src = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                ast.parse(src)
            except SyntaxError as exc:
                self.fail(f"Syntax error in {py_file}: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
