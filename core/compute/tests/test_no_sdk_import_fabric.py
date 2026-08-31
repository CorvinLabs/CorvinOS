"""AST walk: verify no SDK imports in corvin_compute/fabric/ (ADR-0026 constraint).

Walks all .py files under corvin_compute/fabric/ and asserts:
  - no `import anthropic`
  - no `import openai`
  - no `import google.cloud.aiplatform`
  - no `boto3.client("bedrock")`
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

# Locate the fabric package relative to this test file
_HERE = Path(__file__).parent
_FABRIC_ROOT = _HERE.parent / "corvin_compute" / "fabric"


def _collect_py_files() -> list[Path]:
    """Recursively collect all .py files under corvin_compute/fabric/."""
    return sorted(_FABRIC_ROOT.rglob("*.py"))


def _check_no_sdk(path: Path) -> list[str]:
    """Return list of violation descriptions for path, or empty list."""
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        violations.append(f"SyntaxError: {exc}")
        return violations

    for node in ast.walk(tree):
        # import X or import X.Y
        if isinstance(node, ast.Import):
            for alias in node.names:
                n = alias.name
                if _is_forbidden(n):
                    violations.append(
                        f"line {node.lineno}: `import {n}` is forbidden"
                    )
        # from X import Y
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden(module):
                violations.append(
                    f"line {node.lineno}: `from {module} import ...` is forbidden"
                )
        # boto3.client("bedrock")
        elif isinstance(node, ast.Call):
            src = ast.unparse(node)
            if 'boto3' in src and 'bedrock' in src:
                violations.append(
                    f"line {node.lineno}: boto3 bedrock call detected"
                )

    return violations


def _is_forbidden(module_name: str) -> bool:
    return (
        module_name == "anthropic"
        or module_name.startswith("anthropic.")
        or module_name == "openai"
        or module_name.startswith("openai.")
        or module_name == "google.cloud.aiplatform"
        or module_name.startswith("google.cloud.aiplatform.")
    )


def test_fabric_has_python_files():
    """Sanity check: the fabric directory has Python files."""
    files = _collect_py_files()
    assert len(files) > 0, f"No .py files found under {_FABRIC_ROOT}"


@pytest.mark.parametrize("py_file", _collect_py_files())
def test_no_sdk_import(py_file: Path):
    """Each fabric file must not import any forbidden SDK."""
    violations = _check_no_sdk(py_file)
    rel = py_file.relative_to(_HERE.parent)
    assert not violations, (
        f"{rel} violates SDK import constraint:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
