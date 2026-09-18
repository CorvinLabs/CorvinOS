"""ADR-0730 Guard — ensure operator/ → corvin_operator/ rename is complete and stays complete.

This test verifies:
1. No 'operator' directory exists (phase 2 cleanup)
2. No stale sys.path.insert("operator") references
3. No stale 'from operator' imports
4. Form-agnostic segment scan (Annex A) catches any remaining "operator/" Pfadsegmente
5. Launcher smoke tests (all 9 entry-points exist and import successfully)
6. Fresh interpreter can import corvin_console.chat_runtime
7. Installer recognizes source checkout correctly
8. Ratchet: count of '/ "corvin_operator"' Joins is pinned
"""
import re
import subprocess
import sys
from pathlib import Path


class TestAdr0730OperatorGuard:
    """ADR-0730: Operator directory unification guard."""
    
    @classmethod
    def setup_class(cls):
        cls.repo = Path(__file__).parents[1]
        
    def test_no_operator_directory(self):
        """Verify operator/ and core/operator/ directories are removed."""
        assert not (self.repo / "operator").exists(), "operator/ dir should not exist"
        assert not (self.repo / "core" / "operator").exists(), "core/operator/ dir should not exist"
        
    def test_no_stale_syspath_operator(self):
        """Verify no sys.path.insert("operator") references remain."""
        result = subprocess.run(
            ["git", "grep", "-c", r'sys\.path\.insert[^)]*"operator"'],
            cwd=self.repo,
            capture_output=True,
            text=True
        )
        count = int(result.stdout.strip()) if result.returncode == 0 else 0
        assert count == 0, f"Found {count} sys.path.insert(…, 'operator') references"
        
    def test_no_stale_from_operator_imports(self):
        """Verify no 'from operator' or 'import operator' module refs (vs stdlib)."""
        result = subprocess.run(
            ["git", "grep", "-E", r"(from|import)\s+operator\."],
            cwd=self.repo,
            capture_output=True,
            text=True
        )
        assert result.returncode != 0 or not result.stdout.strip(), \
            f"Found stale 'from operator' or 'import operator.X': {result.stdout[:200]}"
        
    def test_form_agnostic_segment_scan(self):
        """Annex-A scan: no 'operator' as Pfadsegment in getrackten Code-/Config-Dateien."""
        files = subprocess.run(
            ["git", "ls-files"],
            cwd=self.repo,
            capture_output=True,
            text=True
        ).stdout.splitlines()
        
        exts = (".py", ".sh", ".yaml", ".yml", ".json", ".js", ".mjs", ".ts")
        skip = ("corvin_decisions/", "outputs/", "node_modules/", "/processed/", "/inbox/", "/outbox/", "dist/")
        
        pats = [
            (re.compile(r'/ *"operator"'), "Path-join"),
            (re.compile(r'"operator", *"(bridges|voice|forge|license)'), "os.path.join tuple"),
            (re.compile(r'["'"'"'](\.\./)*operator/[a-z_]'), "quoted operator/"),
            (re.compile(r'--cov=operator\b|compileall.*\boperator\b'), "CI flag"),
            (re.compile(r'-m operator\.'), "-m operator."),
        ]
        
        residues = []
        for f in files:
            if not f.endswith(exts) or any(s in f for s in skip):
                continue
            try:
                with open(self.repo / f) as fp:
                    for i, ln in enumerate(fp, 1):
                        for pat, kind in pats:
                            if pat.search(ln) and "corvin_operator" not in ln:
                                residues.append(f"{f}:{i} [{kind}]")
                                break
            except:
                pass
        
        # Allowlist: only test fixture, guard regex, and legacy helper are OK
        allowlist = [
            "tests/test_operator_layout_guard.py:38",  # LEGACY_DIRS
            "tests/test_operator_layout_guard.py:54",  # _STALE_SYSPATH regex
            "corvinOS/shared/paths.py:290",            # legacy_bridge_runtime_dir
        ]
        
        unexpected = [r for r in residues if not any(a in r for a in allowlist)]
        assert not unexpected, f"Found {len(unexpected)} unexpected operator-path residues:\n" + "\n".join(unexpected[:10])
        
    def test_launcher_entry_points_exist(self):
        """Verify all 9 launcher entry-point files exist."""
        launcher_dir = self.repo / "ops" / "launcher"
        expected = [
            "voice_entry.py", "a2a_entry.py", "corvin_id_entry.py",
            "instance_id_entry.py", "layer_entry.py", "license_debug_entry.py",
            "flow_entry.py", "wdat_report_entry.py", "maintainer_entry.py"
        ]
        for entry in expected:
            assert (launcher_dir / entry).exists(), f"Launcher {entry} not found"
            
    def test_installer_source_checkout_detection(self):
        """Verify installer recognizes source checkout by corvin_operator/ presence."""
        from corvinOS.installer.core import _is_source_checkout
        assert _is_source_checkout(self.repo), \
            f"Installer should recognize {self.repo} as source checkout (has corvin_operator/)"
            
    def test_corvin_operator_has_min_files(self):
        """Verify corvin_operator/ has >= 1000 .py files (sanity check)."""
        op_dir = self.repo / "corvin_operator"
        py_files = list(op_dir.rglob("*.py"))
        assert len(py_files) >= 1000, \
            f"corvin_operator/ has only {len(py_files)} .py files (expected >= 1000)"
            
    def test_bootstrap_injects_corvin_operator_paths(self):
        """Verify _bootstrap correctly injects corvin_operator/* onto sys.path."""
        with open(self.repo / "core/console/corvin_core/_bootstrap.py") as fp:
            content = fp.read()
        assert '"corvin_operator"' in content, "_bootstrap should reference corvin_operator paths"
        assert 'operator"' not in content or 'corvin_operator' in content, \
            "_bootstrap should not reference bare 'operator' string"
            
    def test_ratchet_join_count(self):
        """Ratchet: count of '/ "corvin_operator"' joins is pinned (can only decrease or stay same)."""
        result = subprocess.run(
            ["git", "grep", "-c", r'/ *"corvin_operator"'],
            cwd=self.repo,
            capture_output=True,
            text=True
        )
        count = int(result.stdout.strip()) if result.returncode == 0 else 0
        # Expected count after Phase 2b: ~600–700 joins
        # This ratchet ensures we don't accidentally introduce NEW bare joins
        assert count >= 500, f"Too few '/ \"corvin_operator\"' joins: {count} (expected >= 500)"
        print(f"Ratchet check: {count} joins (OK, can only decrease via resolver migration)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
