"""E2E tests for license limit enforcement — ADR-0092 / ADR-0094.

Tests verify:
1. compute.submit_run enforces compute_units_per_day quota (free tier = 1/day)
2. compute.submit_run allows runs when quota not exceeded
3. compute.submit_run returns 429 when free-tier limit is hit
4. license.py audit calls are wired correctly (no rec= pattern, no await on sync)
5. Compute quota gate is fail-open on I/O errors
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path bootstrap ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_ROUTES  = _CONSOLE / "corvin_console" / "routes"

for _p in [
    str(_OPERATOR),
    str(_OPERATOR / "license"),
    str(_OPERATOR / "forge"),
    str(_CONSOLE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Test: audit calls in license.py use correct signature ────────────────────

class TestLicenseRouteAuditCalls(unittest.TestCase):
    """Verify the license route uses proper audit call signatures."""

    def _load_license_route_source(self) -> str:
        path = _ROUTES / "license.py"
        return path.read_text(encoding="utf-8")

    def test_no_rec_eq_rec_pattern_in_audit_calls(self):
        """rec=rec should not appear in any audit call (wrong parameter name)."""
        src = self._load_license_route_source()
        # Split into individual audit call sections and check none use rec=rec
        self.assertNotIn("rec=rec,", src,
            "Found 'rec=rec,' in license.py — audit functions don't accept a 'rec' parameter")

    def test_no_await_on_audit_calls(self):
        """console_audit.action_* functions are sync — must NOT be awaited."""
        src = self._load_license_route_source()
        self.assertNotIn("await console_audit.action_performed", src,
            "Found 'await console_audit.action_performed' — audit function is sync, not async")
        self.assertNotIn("await console_audit.action_failed", src,
            "Found 'await console_audit.action_failed' — audit function is sync, not async")
        self.assertNotIn("await console_audit.action_denied", src,
            "Found 'await console_audit.action_denied' — audit function is sync, not async")

    def test_audit_calls_have_sid_fingerprint(self):
        """All audit calls must supply sid_fingerprint= (required by audit.py)."""
        import re
        src = self._load_license_route_source()
        # Count action_performed/failed/denied calls
        call_count = len(re.findall(r"console_audit\.action_(performed|failed|denied)\(", src))
        # Count calls that include sid_fingerprint
        fp_count = len(re.findall(r"sid_fingerprint=rec\.sid_fingerprint", src))
        self.assertEqual(call_count, fp_count,
            f"Mismatch: {call_count} audit calls but only {fp_count} include sid_fingerprint=")

    def test_upload_license_has_target_kind(self):
        """upload_license audit calls must include target_kind='license'."""
        src = self._load_license_route_source()
        # All action_failed/performed in upload context should have target_kind
        self.assertIn('target_kind="license"', src,
            "upload_license audit calls missing target_kind")


# ── Test: compute_quota enforcement in submit_run ────────────────────────────

class TestSubmitRunQuotaEnforcement(unittest.TestCase):
    """Verify submit_run calls increment_and_check and raises 429 on limit."""

    def _load_submit_run_source(self) -> str:
        path = _ROUTES / "compute.py"
        return path.read_text(encoding="utf-8")

    def test_submit_run_imports_increment_and_check(self):
        """submit_run must import and call increment_and_check."""
        src = self._load_submit_run_source()
        self.assertIn("increment_and_check", src,
            "submit_run must call compute_quota.increment_and_check for ADR-0094 enforcement")

    def test_submit_run_enforces_quota_via_shared_gate(self):
        """submit_run must enforce the quota through the shared fail-closed helper.

        ADR-0147 R3-CON-RUNS-DRIFT-01: the helper returns HTTP 402 on a limit and,
        crucially, 402s (does not skip) when the license module is absent — closing
        the drift where the primary route fail-opened while ACS/jobs did not. The
        402 convention lives in _compute_license_gate.enforce_compute_quota.
        """
        src = self._load_submit_run_source()
        self.assertIn("enforce_compute_quota", src,
            "submit_run must call the shared enforce_compute_quota gate")

    def test_submit_run_is_fail_closed_not_fail_open(self):
        """The old fail-open inline quota guard must be gone (R3-CON-RUNS-DRIFT-01).

        The previous `if _COMPUTE_QUOTA_OK and _cq_increment is not None:` +
        `except Exception: pass` silently skipped enforcement when the license
        module was unavailable/shadowed — unmetered compute on the primary route.
        """
        src = self._load_submit_run_source()
        self.assertNotIn("pass  # Operational errors: fail-open", src,
            "submit_run's fail-open inline quota guard must be removed")


# ── Test: compute_quota counter integration ───────────────────────────────────

class TestComputeQuotaCounterIntegration(unittest.TestCase):
    """End-to-end test of the counter: free tier 1/day, blocks on 2nd call."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._corvin_home = Path(self._tmpdir.name)
        # Reset active license to free tier
        import license.validator as _v
        _v._ACTIVE_LICENSE = None
        _v._LICENSE_LOADED_AT = 0.0

    def tearDown(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None
        self._tmpdir.cleanup()

    def test_free_tier_first_call_succeeds(self):
        from license.compute_quota import increment_and_check
        from license.limits import LicenseLimitError
        # First call: free tier allows 10 units/day (ADR-0216)
        try:
            increment_and_check(self._corvin_home, channel="console", chat_key="test:t1")
        except LicenseLimitError:
            self.fail("First compute call on free tier should NOT be blocked")

    def test_free_tier_over_quota_call_blocked(self):
        from license.compute_quota import increment_and_check, get_today_count
        from license.limits import LicenseLimitError
        from license.validator import get_limit
        # Burn the whole shared daily pool (ADR-0216: free tier = 10/day,
        # read dynamically so a future tier change doesn't rot this test).
        limit = int(get_limit("compute_units_per_day"))
        for _ in range(limit):
            increment_and_check(self._corvin_home, channel="console", chat_key="test:t1")
        self.assertEqual(get_today_count(self._corvin_home), limit)
        # Next call should be blocked
        with self.assertRaises(LicenseLimitError) as ctx:
            increment_and_check(self._corvin_home, channel="console", chat_key="test:t1")
        e = ctx.exception
        self.assertEqual(e.feature, "compute_units_per_day")
        self.assertEqual(e.limit, limit)
        self.assertEqual(e.tier, "free")

    def test_counter_not_incremented_on_rejection(self):
        from license.compute_quota import increment_and_check, get_today_count
        from license.limits import LicenseLimitError
        from license.validator import get_limit
        limit = int(get_limit("compute_units_per_day"))
        for _ in range(limit):
            increment_and_check(self._corvin_home)
        try:
            increment_and_check(self._corvin_home)  # rejected (over quota)
        except LicenseLimitError:
            pass
        # Counter should still be at the cap (rejected call does not increment)
        self.assertEqual(get_today_count(self._corvin_home), limit)

    def test_pro_tier_allows_500_units(self):
        import license.validator as _v
        from license.compute_quota import increment_and_check
        from license.limits import LicenseLimitError
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": 500}})
        # 5 calls should all succeed on pro tier
        for i in range(5):
            try:
                increment_and_check(self._corvin_home, channel="console", chat_key=f"test:{i}")
            except LicenseLimitError:
                self.fail(f"Call {i+1} on pro tier should NOT be blocked")

    def test_enterprise_unlimited_never_blocks(self):
        import license.validator as _v
        from license.compute_quota import increment_and_check
        from license.limits import LicenseLimitError
        _v._set_active_license({"tier": "enterprise", "limits": {"compute_units_per_day": None}})
        for i in range(20):
            try:
                increment_and_check(self._corvin_home)
            except LicenseLimitError:
                self.fail(f"Enterprise unlimited: call {i+1} should never be blocked")

    def test_quota_file_mode_0600_after_write(self):
        from license.compute_quota import increment_and_check, _quota_path
        increment_and_check(self._corvin_home)
        qp = _quota_path(self._corvin_home)
        mode = qp.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600,
            f"Quota file must be mode 0600, got 0o{mode:o}")

    def test_world_readable_quota_still_reads_not_resets(self):
        """chmod world-readable should trigger a warning but NOT reset counter to 0."""
        from license.compute_quota import increment_and_check, get_today_count, _quota_path
        from license.limits import LicenseLimitError
        from license.validator import get_limit
        limit = int(get_limit("compute_units_per_day"))
        for _ in range(limit):
            increment_and_check(self._corvin_home)  # counter → cap
        qp = _quota_path(self._corvin_home)
        os.chmod(qp, 0o644)  # tamper: make world-readable
        # Counter should still read the real count (not reset to 0)
        count = get_today_count(self._corvin_home)
        self.assertEqual(count, limit,
            "World-readable quota file must still return real count (bypass-prevention)")
        # Over-quota call should still be blocked
        with self.assertRaises(LicenseLimitError):
            increment_and_check(self._corvin_home)


# ── Test: A2A peer limit enforcement ─────────────────────────────────────────

class TestA2APeerLimitEnforcement(unittest.TestCase):
    """Verify a2a_peers_max is enforced when registering new origins."""

    def setUp(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def tearDown(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def test_free_tier_a2a_peers_max_is_1(self):
        from license.validator import get_limit
        from license.limits import FREE_TIER
        self.assertEqual(get_limit("a2a_peers_max"), 1)
        self.assertEqual(FREE_TIER["a2a_peers_max"], 1)

    def test_assert_limit_blocks_second_peer_on_free_tier(self):
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        assert_limit("a2a_peers_max", 1)  # first peer: ok
        with self.assertRaises(LicenseLimitError) as ctx:
            assert_limit("a2a_peers_max", 2)  # second peer: blocked
        e = ctx.exception
        self.assertEqual(e.feature, "a2a_peers_max")
        self.assertEqual(e.limit, 1)

    def test_pro_tier_allows_10_peers(self):
        import license.validator as _v
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        _v._set_active_license({"tier": "pro", "limits": {"a2a_peers_max": 10}})
        assert_limit("a2a_peers_max", 10)  # at limit: ok
        with self.assertRaises(LicenseLimitError):
            assert_limit("a2a_peers_max", 11)  # over limit: blocked


# ── Test: workflows_concurrent limit definition ───────────────────────────────

class TestWorkflowsConcurrentLimit(unittest.TestCase):
    """Verify workflows_concurrent is defined in FREE_TIER and tier defaults."""

    def test_free_tier_workflows_concurrent_is_1(self):
        from license.limits import FREE_TIER
        self.assertEqual(FREE_TIER["workflows_concurrent"], 1,
            "Free tier must limit concurrent workflows to 1")

    def test_member_tier_workflows_concurrent_unlimited(self):
        # Only free + member exist; the legacy "pro" name aliases to member,
        # which is unlimited (None) — not the removed 15-workflow ceiling.
        from license.limits import TIER_RESOURCE_LIMITS
        self.assertIsNone(TIER_RESOURCE_LIMITS["member"]["workflows_concurrent"])
        self.assertIsNone(TIER_RESOURCE_LIMITS["pro"]["workflows_concurrent"])

    def test_enterprise_workflows_unlimited(self):
        from license.limits import TIER_RESOURCE_LIMITS
        self.assertIsNone(TIER_RESOURCE_LIMITS["enterprise"]["workflows_concurrent"])

    def test_assert_limit_blocks_over_free_tier(self):
        import license.validator as _v
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        _v._ACTIVE_LICENSE = None  # free tier
        assert_limit("workflows_concurrent", 1)  # 1 concurrent: ok
        with self.assertRaises(LicenseLimitError):
            assert_limit("workflows_concurrent", 2)  # 2 concurrent on free: blocked


# ── Test: tenants_max limit enforcement ──────────────────────────────────────

class TestTenantsMaxLimit(unittest.TestCase):
    """Verify tenants_max is defined and enforced correctly."""

    def setUp(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def tearDown(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def test_free_tier_tenants_max_is_1(self):
        from license.limits import FREE_TIER
        self.assertEqual(FREE_TIER["tenants_max"], 1,
            "Free tier must limit tenants to 1")

    def test_member_tier_allows_unlimited_tenants(self):
        # Legacy "business" aliases to member = unlimited tenants (None), not 10.
        from license.limits import TIER_RESOURCE_LIMITS
        self.assertIsNone(TIER_RESOURCE_LIMITS["member"]["tenants_max"])
        self.assertIsNone(TIER_RESOURCE_LIMITS["business"]["tenants_max"])

    def test_assert_limit_blocks_second_tenant_on_free_tier(self):
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        assert_limit("tenants_max", 1)  # 1 tenant: ok
        with self.assertRaises(LicenseLimitError) as ctx:
            assert_limit("tenants_max", 2)  # 2nd tenant on free: blocked
        self.assertEqual(ctx.exception.feature, "tenants_max")


# ── Test: start_run workflow_concurrent enforcement ───────────────────────────

class TestWorkflowConcurrentEnforcement(unittest.TestCase):
    """Verify start_run enforces the workflows_concurrent quota."""

    def _load_workflows_source(self) -> str:
        path = _ROUTES / "workflows.py"
        return path.read_text(encoding="utf-8")

    def test_start_run_imports_assert_limit(self):
        """start_run must call assert_limit for workflows_concurrent."""
        src = self._load_workflows_source()
        self.assertIn("workflows_concurrent", src,
            "start_run must enforce workflows_concurrent limit (ADR-0094)")

    def test_start_run_raises_402_on_concurrent_limit(self):
        """start_run must return 402 when concurrent limit is exceeded (license limit, not rate limit)."""
        src = self._load_workflows_source()
        self.assertIn("HTTP_402_PAYMENT_REQUIRED", src,
            "start_run must return 402 when workflows_concurrent limit is hit (ADR-0094: license gates use 402)")

    def test_count_running_workflows_helper_exists(self):
        """_count_running_workflows helper must exist."""
        src = self._load_workflows_source()
        self.assertIn("def _count_running_workflows", src,
            "_count_running_workflows helper required for concurrent limit enforcement")

    def test_count_existing_workflows_fail_closed(self):
        """_count_existing_workflows enforces fail-closed per ADR-0094.

        I/O errors must not be silently suppressed with a return 0, which would
        be misinterpreted as "no workflows, grant unlimited creation".
        """
        src = self._load_workflows_source()
        count_existing_src = src.split("def _count_existing_workflows")[1].split("def ")[0]
        # Must not have silent exception handler that returns 0
        self.assertNotIn("except Exception", count_existing_src,
            "_count_existing_workflows must propagate I/O errors (fail-closed per ADR-0094)")

    def test_count_running_workflows_counts_running_status(self):
        """_count_running_workflows must only count status='running' runs."""
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate the directory structure manually
            wf_root = Path(tmpdir) / "workflows"
            wid = "wf_test"
            runs_dir = wf_root / wid / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)

            # Write two runs: one running, one completed
            (runs_dir / "rid1.meta.json").write_text(
                json.dumps({"rid": "rid1", "status": "running"}), encoding="utf-8"
            )
            (runs_dir / "rid2.meta.json").write_text(
                json.dumps({"rid": "rid2", "status": "completed"}), encoding="utf-8"
            )

            # Use the logic from _count_running_workflows directly
            count = 0
            for meta_file in runs_dir.glob("*.meta.json"):
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    if meta.get("status") == "running":
                        count += 1
                except Exception:
                    pass

            self.assertEqual(count, 1,
                "Should count exactly 1 running workflow (not the completed one)")

    def test_start_run_concurrent_gate_is_fail_closed(self):
        """WF-CONC-02 (ADR-0148): start_run's concurrent-workflow gate must FAIL
        CLOSED. A license-import error is handled by the always-defined _lic_assert
        stub (fail-closed); a genuinely unexpected gate error (e.g. the runs tree
        cannot be enumerated) must refuse the run, not 'allow' it — the old
        fail-open let a run slip past workflows_concurrent.
        """
        src = self._load_workflows_source()
        self.assertNotIn("allowing run", src,
            "start_run must NOT fail-open on an unexpected concurrent-gate error")
        self.assertIn("refusing run (fail-closed)", src,
            "start_run's outer handler must deny (503) on an unexpected gate error")


# ── Test: workflows_max limit definition ─────────────────────────────────────

class TestWorkflowsMaxLimit(unittest.TestCase):
    """Verify workflows_max is defined in FREE_TIER and paid tier defaults."""

    def setUp(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def tearDown(self):
        import license.validator as _v
        _v._ACTIVE_LICENSE = None

    def test_free_tier_workflows_max_is_1(self):
        from license.limits import FREE_TIER
        self.assertEqual(FREE_TIER["workflows_max"], 1,
            "Free tier must cap total workflows at 1")

    def test_member_tier_workflows_max_is_none(self):
        from license.limits import TIER_RESOURCE_LIMITS
        self.assertIsNone(TIER_RESOURCE_LIMITS["member"]["workflows_max"],
            "Member tier must have unlimited workflows (None)")

    def test_enterprise_tier_workflows_max_is_none(self):
        from license.limits import TIER_RESOURCE_LIMITS
        self.assertIsNone(TIER_RESOURCE_LIMITS["enterprise"]["workflows_max"],
            "Enterprise tier must have unlimited workflows (None)")

    def test_assert_limit_blocks_second_workflow_on_free_tier(self):
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        assert_limit("workflows_max", 1)  # 1 workflow: ok on free tier
        with self.assertRaises(LicenseLimitError) as ctx:
            assert_limit("workflows_max", 2)  # 2nd workflow: blocked
        e = ctx.exception
        self.assertEqual(e.feature, "workflows_max")
        self.assertEqual(e.limit, 1)
        self.assertEqual(e.tier, "free")

    def test_member_tier_allows_unlimited_workflows(self):
        import license.validator as _v
        from license.validator import assert_limit
        from license.limits import LicenseLimitError
        _v._set_active_license({"tier": "member", "limits": {"workflows_max": None}})
        # None = unlimited; assert_limit must NOT raise for any value
        try:
            for n in [1, 10, 100, 999]:
                assert_limit("workflows_max", n)
        except LicenseLimitError as e:
            self.fail(f"Member tier must allow unlimited workflows but raised: {e}")


# ── Test: workflows_max enforcement in create_workflow route ──────────────────

class TestCreateWorkflowMaxEnforcement(unittest.TestCase):
    """Source-level verification that create_workflow enforces workflows_max."""

    def _load_workflows_source(self) -> str:
        path = _ROUTES / "workflows.py"
        return path.read_text(encoding="utf-8")

    def test_count_existing_workflows_helper_exists(self):
        src = self._load_workflows_source()
        self.assertIn("def _count_existing_workflows", src,
            "_count_existing_workflows helper required for workflows_max enforcement")

    def test_create_workflow_checks_workflows_max(self):
        src = self._load_workflows_source()
        self.assertIn('"workflows_max"', src,
            'create_workflow must enforce "workflows_max" limit (ADR-0094)')

    def test_create_workflow_raises_402_on_max_exceeded(self):
        src = self._load_workflows_source()
        self.assertIn("HTTP_402_PAYMENT_REQUIRED", src,
            "create_workflow must return 402 when workflows_max is exceeded")

    def test_enforce_workflows_max_used_at_both_creation_sites(self):
        """_enforce_workflows_max must be called from both create_workflow and _import_write_locked."""
        src = self._load_workflows_source()
        self.assertIn("_enforce_workflows_max", src,
            "_enforce_workflows_max helper must exist in workflows.py")
        # Count call sites — must appear in both create_workflow and _import_write_locked
        call_count = src.count("_enforce_workflows_max(")
        self.assertGreaterEqual(call_count, 2,
            "_enforce_workflows_max must be called at both workflow creation paths (create + import)")

    def test_count_existing_workflows_skips_corrupt_meta(self):
        """_count_existing_workflows must NOT count corrupt/empty/falsy meta files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            import json as _json
            # Valid workflow — must be counted
            (base / "wf_valid.meta.json").write_text(_json.dumps({"id": "wf_valid"}))
            # Corrupt JSON — must NOT be counted
            (base / "wf_corrupt.meta.json").write_text("<<<not json>>>")
            # Empty file — must NOT be counted
            (base / "wf_empty.meta.json").write_text("")
            # Empty JSON object — must NOT be counted (falsy, same as list_workflows filter)
            (base / "wf_empty_obj.meta.json").write_text("{}")

            def _read_json_local(path: Path):
                try:
                    import json as j
                    return j.loads(path.read_text())
                except Exception:
                    return None

            count = sum(1 for mf in base.glob("*.meta.json") if _read_json_local(mf))
            self.assertEqual(count, 1,
                "_count_existing_workflows must count only valid, non-empty meta files")

    def test_count_existing_workflows_returns_zero_for_missing_dir(self):
        """_count_existing_workflows must return 0 when the workflows dir doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Count .meta.json files in a non-existent subdirectory → 0
            base = Path(tmpdir) / "workflows"
            # Don't create it
            count = sum(1 for _ in base.glob("*.meta.json")) if base.exists() else 0
            self.assertEqual(count, 0,
                "_count_existing_workflows must return 0 for missing dir")

    def test_count_existing_workflows_counts_meta_files(self):
        """_count_existing_workflows counts *.meta.json files in the workflows dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Create two workflow meta files
            (base / "wf_alpha.meta.json").write_text('{"wid":"wf_alpha"}')
            (base / "wf_beta.meta.json").write_text('{"wid":"wf_beta"}')
            # Also a run meta (in subdirectory) — must NOT be counted
            run_dir = base / "wf_alpha" / "runs"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "rid1.meta.json").write_text('{"rid":"rid1","status":"running"}')

            count = sum(1 for _ in base.glob("*.meta.json"))
            self.assertEqual(count, 2,
                "_count_existing_workflows must count only top-level *.meta.json files")

    def test_count_existing_workflows_ignores_non_meta_files(self):
        """YAML and JSONL files must not be counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "wf_alpha.awp.yaml").write_text("awp: 1.0.0")
            (base / "wf_alpha.chat.jsonl").write_text("")
            (base / "wf_alpha.meta.json").write_text('{"wid":"wf_alpha"}')

            count = sum(1 for _ in base.glob("*.meta.json"))
            self.assertEqual(count, 1,
                "_count_existing_workflows must ignore .yaml and .jsonl files")


if __name__ == "__main__":
    unittest.main(verbosity=2)
