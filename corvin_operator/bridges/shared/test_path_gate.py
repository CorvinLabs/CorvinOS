"""test_path_gate.py — V-007/V-013: path_gate Bash detection for ADR-0072.

Tests that the path_gate check() function correctly DENIES Bash commands that
use opaque write vectors (named pipes, exec fd redirects, process substitution)
targeting protected paths, while ALLOWING benign Bash commands.

CORVIN_HOME is pointed at a tmpdir so that is_protected_path() resolves the
forge/skill-forge/audit.jsonl paths under the tmpdir rather than the real
~/.corvin directory.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Locate path_gate.py relative to this test file.
HERE = Path(__file__).resolve().parent
HOOK_PATH = HERE.parent.parent / "voice" / "hooks" / "path_gate.py"

# Make the hooks package importable.
sys.path.insert(0, str(HOOK_PATH.parent))

import importlib.util as _ilu

# Load path_gate as a module (it is not inside a package, so we use spec).
_spec = _ilu.spec_from_file_location("path_gate", HOOK_PATH)
_pg_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_pg_mod)  # type: ignore[union-attr]
check = _pg_mod.check
is_protected_path = _pg_mod.is_protected_path


def _bash_payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


class PathGateBashDetectionTests(unittest.TestCase):
    """V-007/V-013: Bash commands using opaque write vectors must be denied."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="path-gate-test-")
        # Create the protected subtree so is_protected_path() resolves correctly.
        (Path(self._tmp) / "global" / "forge").mkdir(parents=True)
        (Path(self._tmp) / "global" / "skill-forge").mkdir(parents=True)
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        # Remove CORVIN_HOME override so we don't leak state to other tests.
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Deny cases
    # ------------------------------------------------------------------

    def test_bash_named_pipe_denied(self):
        """V-007: mkfifo + cat redirect into audit.jsonl must be denied."""
        corvin_home = self._tmp
        audit_path = f"{corvin_home}/global/forge/audit.jsonl"
        cmd = f"mkfifo /tmp/p; cat /tmp/p > {audit_path}"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"mkfifo targeting audit.jsonl must be denied; reason: {reason!r}")
        self.assertIn("path_gate", reason,
                      "deny reason must mention path_gate")

    def test_bash_exec_fd_denied(self):
        """V-007: exec N> file-descriptor redirect into a forge tool must be denied."""
        corvin_home = self._tmp
        forge_tool = f"{corvin_home}/global/forge/tool.py"
        cmd = f"exec 3> {forge_tool}; echo x >&3"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"exec fd redirect into forge path must be denied; reason: {reason!r}")

    def test_bash_process_sub_write_denied(self):
        """V-013: process substitution writing into skill-forge/SKILL.md must be denied."""
        corvin_home = self._tmp
        skill_md = f"{corvin_home}/global/skill-forge/SKILL.md"
        cmd = f"echo x | tee >(cat > {skill_md})"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"process substitution into skill-forge path must be denied; "
                         f"reason: {reason!r}")

    def test_bash_process_sub_audit_denied(self):
        """V-007: >(cat > audit.jsonl) is a protected path via process substitution."""
        corvin_home = self._tmp
        audit_path = f"{corvin_home}/global/forge/audit.jsonl"
        cmd = f"some_cmd | tee >(cat > {audit_path})"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"process substitution into audit.jsonl must be denied; "
                         f"reason: {reason!r}")

    def test_bash_redirect_into_forge_denied(self):
        """Standard redirect > into a forge workspace must be denied."""
        corvin_home = self._tmp
        forge_file = f"{corvin_home}/global/forge/evil.py"
        cmd = f"echo 'import os; os.system(\"rm -rf /\")' > {forge_file}"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"direct redirect into forge path must be denied; reason: {reason!r}")

    # ------------------------------------------------------------------
    # Allow cases (no false positives)
    # ------------------------------------------------------------------

    def test_bash_safe_echo_allowed(self):
        """Benign echo command with no protected path must be allowed."""
        cmd = "echo hello"
        allowed, reason = check(_bash_payload(cmd))
        self.assertTrue(allowed,
                        f"plain 'echo hello' must be allowed; reason: {reason!r}")

    def test_bash_ls_allowed(self):
        """ls of a non-protected directory must be allowed."""
        cmd = "ls /tmp"
        allowed, reason = check(_bash_payload(cmd))
        self.assertTrue(allowed,
                        f"'ls /tmp' must be allowed; reason: {reason!r}")

    def test_bash_redirect_to_tmp_allowed(self):
        """Redirect to /tmp (non-protected) must be allowed."""
        cmd = "echo data > /tmp/safe_output.txt"
        allowed, reason = check(_bash_payload(cmd))
        self.assertTrue(allowed,
                        f"redirect to /tmp must be allowed; reason: {reason!r}")

    def test_bash_empty_command_allowed(self):
        """Empty Bash command string must be treated as allow (nothing to deny)."""
        cmd = ""
        allowed, reason = check(_bash_payload(cmd))
        self.assertTrue(allowed,
                        f"empty command must be allowed; reason: {reason!r}")

    # ------------------------------------------------------------------
    # is_protected_path sanity checks under tmpdir CORVIN_HOME
    # ------------------------------------------------------------------

    def test_forge_path_is_protected(self):
        """A path under <CORVIN_HOME>/global/forge/ must be detected as protected."""
        corvin_home = self._tmp
        p = f"{corvin_home}/global/forge/somefile.py"
        self.assertTrue(is_protected_path(p),
                        f"forge path should be protected: {p}")

    def test_skill_forge_path_is_protected(self):
        """A path under <CORVIN_HOME>/global/skill-forge/ must be protected."""
        corvin_home = self._tmp
        p = f"{corvin_home}/global/skill-forge/SKILL.md"
        self.assertTrue(is_protected_path(p),
                        f"skill-forge path should be protected: {p}")

    def test_tmp_path_not_protected(self):
        """A plain /tmp path must NOT be flagged as protected."""
        p = "/tmp/harmless.txt"
        self.assertFalse(is_protected_path(p),
                         f"/tmp path must not be protected: {p}")

    # ------------------------------------------------------------------
    # Coverage gap closure: Edge cases + error paths (Iteration 6)
    # ------------------------------------------------------------------

    def test_bash_sed_inline_edit_denied(self):
        """V-007: sed -i /path must be caught (in-place editing into protected path)."""
        corvin_home = self._tmp
        policy_file = f"{corvin_home}/global/policy.json"
        cmd = f"sed -i 's/old/new/g' {policy_file}"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"sed -i into protected path must be denied; reason: {reason!r}")

    def test_bash_empty_protected_path_edge_case(self):
        """is_protected_path("") must not crash and should return False."""
        result = is_protected_path("")
        self.assertFalse(result,
                         "empty path must not be considered protected")

    def test_bash_relative_path_resolution(self):
        """Relative path like ./forge/tool.py must be caught if it resolves to protected."""
        corvin_home = self._tmp
        # Change to CORVIN_HOME so ./global/forge resolves correctly
        old_cwd = os.getcwd()
        try:
            os.chdir(self._tmp)
            cmd = "echo code > ./global/forge/tool.py"
            allowed, reason = check(_bash_payload(cmd))
            self.assertFalse(allowed,
                             f"relative path into forge must be denied; reason: {reason!r}")
        finally:
            os.chdir(old_cwd)

    def test_bash_python_open_simple_write_denied(self):
        """Python open(..., 'w') into a protected path must be caught."""
        corvin_home = self._tmp
        audit_path = f"{corvin_home}/global/forge/audit.jsonl"
        cmd = f"python3 -c \"open('{audit_path}', 'w').write('x')\""
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"Python open() into protected path must be denied; reason: {reason!r}")

    def test_bash_eval_exec_nesting_denied(self):
        """eval/exec wrapping write redirects must be denied."""
        corvin_home = self._tmp
        audit_path = f"{corvin_home}/global/forge/audit.jsonl"
        cmd = f"eval \"echo x > {audit_path}\""
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"eval wrapping redirect must be denied; reason: {reason!r}")

    def test_bash_backtick_command_substitution_denied(self):
        """Backtick command substitution with redirect into protected path must be denied."""
        corvin_home = self._tmp
        skill_path = f"{corvin_home}/global/skill-forge/SKILL.md"
        cmd = f"`cat > {skill_path}`"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"backtick command substitution into protected path must be denied; "
                         f"reason: {reason!r}")

    # ------------------------------------------------------------------
    # ADR-0145: IBC key protection
    # ------------------------------------------------------------------

    def test_instance_key_pem_is_protected(self):
        """ADR-0145: Ed25519 instance signing key must be path-gate protected."""
        corvin_home = self._tmp
        p = f"{corvin_home}/global/instance_key.pem"
        self.assertTrue(is_protected_path(p),
                        f"instance_key.pem should be protected: {p}")

    def test_instance_cert_jwt_is_protected(self):
        """ADR-0145: IBC JWT must be path-gate protected."""
        corvin_home = self._tmp
        p = f"{corvin_home}/global/instance_cert.jwt"
        self.assertTrue(is_protected_path(p),
                        f"instance_cert.jwt should be protected: {p}")

    def test_instance_pubkey_pem_is_protected(self):
        """ADR-0145: Ed25519 public key companion must be path-gate protected."""
        corvin_home = self._tmp
        p = f"{corvin_home}/global/instance_pubkey.pem"
        self.assertTrue(is_protected_path(p),
                        f"instance_pubkey.pem should be protected: {p}")

    def test_bash_write_to_instance_key_denied(self):
        """ADR-0145: Bash redirect into instance_key.pem must be denied."""
        corvin_home = self._tmp
        key_path = f"{corvin_home}/global/instance_key.pem"
        cmd = f"echo 'evil' > {key_path}"
        allowed, reason = check(_bash_payload(cmd))
        self.assertFalse(allowed,
                         f"bash write to instance_key.pem must be denied; reason: {reason!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
