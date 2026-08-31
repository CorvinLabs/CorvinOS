"""ADR-0215 Phase 1: tests for the Wiring Manifest + Reachability Gate.

Two kinds of coverage:
1. The gate's OWN logic, exercised against synthetic fixtures (so a bug in
   the gate itself can't hide behind "the real repo happens to be clean").
2. A live run against the real repo's WIRING.yaml files + the real
   operator/, core/ trees — this is the actual CI gate. It must pass; if it
   doesn't, either a new orphan module or a new dotted `operator.` import
   was introduced without a manifest update (exactly the two bug classes
   ADR-0215 exists to close).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

import wiring_gate  # noqa: E402


# ── Live run against the real repo ──────────────────────────────────────

def test_real_repo_wiring_gate_is_green():
    result = wiring_gate.check_all()
    if not result.ok:
        details = "\n".join(f"  [{f.severity}] {f.message}" for f in result.failures)
        pytest.fail(f"wiring_gate found {len(result.failures)} failure(s):\n{details}")


def test_real_repo_has_no_dotted_operator_imports():
    result = wiring_gate.GateResult()
    wiring_gate.lint_dotted_operator_imports(result)
    assert not result.failures, [f.message for f in result.failures]


def test_real_manifests_are_complete():
    result = wiring_gate.GateResult()
    wiring_gate.check_manifest_completeness(result)
    assert not result.failures, [f.message for f in result.failures]


def test_real_live_entry_points_resolve():
    result = wiring_gate.GateResult()
    wiring_gate.check_live_entry_points(result)
    assert not result.failures, [f.message for f in result.failures]


# ── Gate logic, exercised against synthetic fixtures ────────────────────

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))


def test_undeclared_module_fails_completeness_check(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "known.py", "X = 1\n")
    _write(src_dir / "mystery.py", "Y = 2\n")  # not declared
    _write(manifest, """
        components:
          - name: known
            status: live
            entry_point: "known:X"
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_manifest_completeness(result)
    assert any("mystery.py" in f.message for f in result.failures)


def test_stale_manifest_entry_warns_not_fails(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg2"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "real.py", "X = 1\n")
    _write(manifest, """
        components:
          - name: real
            status: live
            entry_point: "real:X"
          - name: ghost
            status: live
            entry_point: "ghost:X"
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_manifest_completeness(result)
    assert not result.failures  # `real` is fully declared; `ghost` just doesn't exist as a file
    assert any("ghost" in f.message for f in result.warnings)


def test_live_entry_point_that_does_not_import_fails(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg3"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "broken.py", "import totally_nonexistent_module_xyz\n")
    _write(manifest, """
        components:
          - name: broken
            status: live
            entry_point: "broken:whatever"
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_live_entry_points(result)
    assert result.failures
    assert "broken" in result.failures[0].message


def test_live_entry_point_missing_symbol_fails(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg4"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "mod.py", "X = 1\n")
    _write(manifest, """
        components:
          - name: mod
            status: live
            entry_point: "mod:DoesNotExist"
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_live_entry_points(result)
    assert any("no attribute" in f.message for f in result.failures)


def test_deferred_without_reason_fails(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg5"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "future.py", "X = 1\n")
    _write(manifest, """
        components:
          - name: future
            status: deferred
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_deferred_has_reason(result)
    assert result.failures


def test_deferred_with_reason_passes(tmp_path, monkeypatch):
    src_dir = tmp_path / "pkg6"
    manifest = src_dir / "WIRING.yaml"
    _write(src_dir / "future.py", "X = 1\n")
    _write(manifest, """
        components:
          - name: future
            status: deferred
            reason: "genuinely not wired yet, tracked in ADR-XXXX"
    """)
    monkeypatch.setattr(wiring_gate, "_MANIFESTS", ((src_dir, manifest, src_dir),))
    result = wiring_gate.GateResult()
    wiring_gate.check_deferred_has_reason(result)
    assert not result.failures


def test_lint_catches_dotted_import(tmp_path, monkeypatch):
    scan_root = tmp_path / "scan"
    _write(scan_root / "bad.py", """
        from operator.bridges.shared.audit import audit_path
    """)
    monkeypatch.setattr(wiring_gate, "_LINT_SCAN_ROOTS", (scan_root,))
    result = wiring_gate.GateResult()
    wiring_gate.lint_dotted_operator_imports(result)
    assert len(result.failures) == 1
    assert "bad.py" in result.failures[0].message


def test_lint_ignores_stdlib_operator_import(tmp_path, monkeypatch):
    scan_root = tmp_path / "scan2"
    _write(scan_root / "fine.py", """
        from operator import attrgetter
        import operator
    """)
    monkeypatch.setattr(wiring_gate, "_LINT_SCAN_ROOTS", (scan_root,))
    result = wiring_gate.GateResult()
    wiring_gate.lint_dotted_operator_imports(result)
    assert not result.failures


def test_lint_ignores_docstring_and_comment_mentions(tmp_path, monkeypatch):
    # Regression: an earlier regex-based version of this lint flagged
    # operator/license/{sob,capability,seal_loader}.py's module docstrings,
    # which merely quote a broken import as a usage example, not real code.
    scan_root = tmp_path / "scan3"
    _write(scan_root / "docs.py", '''
        """
        Usage::

            from operator.license.sob import SobClient
        """
        # from operator.bridges.shared.audit import audit_path
        X = 1
    ''')
    monkeypatch.setattr(wiring_gate, "_LINT_SCAN_ROOTS", (scan_root,))
    result = wiring_gate.GateResult()
    wiring_gate.lint_dotted_operator_imports(result)
    assert not result.failures


def test_lint_excludes_vendored_dirs(tmp_path, monkeypatch):
    scan_root = tmp_path / "scan4"
    _write(scan_root / "node_modules" / "somepkg" / "bad.py", """
        from operator.bridges.shared.audit import audit_path
    """)
    monkeypatch.setattr(wiring_gate, "_LINT_SCAN_ROOTS", (scan_root,))
    result = wiring_gate.GateResult()
    wiring_gate.lint_dotted_operator_imports(result)
    assert not result.failures
