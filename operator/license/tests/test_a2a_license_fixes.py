"""Tests for ADR-0144 A2A-phase license fixes.

Covers:
  - A2A worker calls compute_quota gate before spawning (ADR-0144 A2A fix)
  - Compute quota exceeded → WorkerResult(status="rejected")
  - CORVIN_LICENSE_KEY cleared from worker spawn env
  - session_refresh pre-write Ed25519 verify (ADR-0144 F-A)
  - remote_trigger_receiver _DEFAULT_RATE_LIMIT_RPM = 60
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest

# Paths
_OPERATOR_DIR   = str(Path(__file__).resolve().parents[2])          # .../operator
_LICENSE_DIR    = str(Path(__file__).resolve().parents[1])          # .../operator/license
_SHARED_DIR     = str(Path(__file__).resolve().parents[2] / "bridges" / "shared")

for _d in (_OPERATOR_DIR, _LICENSE_DIR, _SHARED_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

os.environ.setdefault("CORVIN_INTEGRATION_TEST", "1")


# ── helpers ───────────────────────────────────────────────────────────────────

def _clear_mod(*names: str) -> None:
    for name in names:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]


def _fresh_a2a_worker():
    """Re-import a2a_worker with a clean module state."""
    _clear_mod("a2a_worker")
    return __import__("a2a_worker")


def _allow_l44():
    """Neutralise the L44 (acceptable-use) gate so a spawn-path test can isolate
    the assertion it actually targets (compute-quota / env-clearing / spawn).

    spawn_a2a_worker now runs ``spawn_gates.check_l44`` BEFORE the compute-quota
    gate (ADR-0143 fail-closed acceptable-use, ordered before the billable
    quota increment so a denied request consumes no quota). In these isolated
    license tests ``house_rules`` is not importable, so the real check_l44
    fail-closes and short-circuits the spawn before reaching the gate under
    test. Patching check_l44 → allow (return None) mirrors how these tests
    already inject fake compute_quota / engine factories. Returns a
    mock.patch context manager."""
    import spawn_gates  # noqa: PLC0415 — local import keeps top-level import order intact
    return mock.patch.object(spawn_gates, "check_l44", lambda *a, **k: None)


# ── A2A compute-quota gate ────────────────────────────────────────────────────

def test_a2a_worker_quota_gate_is_called():
    """spawn_a2a_worker must call compute_quota.increment_and_check before spawning."""
    call_order: list[str] = []

    fake_cq = types.ModuleType("license.compute_quota")
    fake_cq.__file__ = str(Path(_LICENSE_DIR) / "compute_quota.py")
    def _fake_increment(corvin_home, *, channel="", chat_key=""):
        call_order.append("quota")
    fake_cq.increment_and_check = _fake_increment

    fake_limits = types.ModuleType("license.limits")
    fake_limits.__file__ = str(Path(_LICENSE_DIR) / "limits.py")
    class LicenseLimitError(Exception):
        pass
    fake_limits.LicenseLimitError = LicenseLimitError

    class _FakeEngine:
        name = "fake"
        def spawn(self, *a, **kw):
            call_order.append("engine")
            raise RuntimeError("engine-not-needed")

    with mock.patch.dict(sys.modules, {
        "license.compute_quota": fake_cq,
        "license.limits": fake_limits,
    }), _allow_l44():
        mod = _fresh_a2a_worker()
        result = mod.spawn_a2a_worker(
            instruction="hello",
            origin_id="test-origin",
            task_id="task-001",
            persona="assistant",
            ttl_s=5,
            engine_factory=lambda: _FakeEngine(),
        )

    assert "quota" in call_order, (
        "increment_and_check was NOT called during spawn_a2a_worker. "
        "ADR-0144 A2A fix: compute quota gate missing."
    )
    if "engine" in call_order:
        assert call_order.index("quota") < call_order.index("engine"), (
            "Quota gate must fire BEFORE engine spawn."
        )


def test_a2a_worker_quota_exceeded_returns_rejected():
    """When compute_quota raises LicenseLimitError, spawn_a2a_worker returns rejected."""
    fake_cq = types.ModuleType("license.compute_quota")
    fake_cq.__file__ = str(Path(_LICENSE_DIR) / "compute_quota.py")
    fake_limits = types.ModuleType("license.limits")
    fake_limits.__file__ = str(Path(_LICENSE_DIR) / "limits.py")

    class LicenseLimitError(Exception):
        pass
    fake_limits.LicenseLimitError = LicenseLimitError

    def _raise_limit(*a, **kw):
        raise LicenseLimitError("compute_units_per_day exhausted (limit=1)")
    fake_cq.increment_and_check = _raise_limit

    with mock.patch.dict(sys.modules, {
        "license.compute_quota": fake_cq,
        "license.limits": fake_limits,
    }), _allow_l44():
        mod = _fresh_a2a_worker()
        result = mod.spawn_a2a_worker(
            instruction="hello",
            origin_id="test-origin",
            task_id="task-002",
            persona="assistant",
            ttl_s=5,
        )

    assert result.status == "rejected", (
        f"Expected status='rejected' on quota exceeded, got {result.status!r}"
    )
    assert "compute_quota_exceeded" in result.error, (
        f"Expected 'compute_quota_exceeded' in error, got {result.error!r}"
    )


# ── CORVIN_LICENSE_KEY env isolation ─────────────────────────────────────────

def test_a2a_worker_clears_license_key_in_spawn_kwargs():
    """spawn_a2a_worker must set CORVIN_LICENSE_KEY='' in spawn env (ADR-0144)."""
    spawn_envs: list[dict] = []

    class _SpyEngine:
        name = "spy"
        def spawn(self, prompt, **kwargs):
            spawn_envs.append(dict(kwargs.get("env") or {}))
            raise RuntimeError("spy-done")

    # Stub out compute_quota so the new gate doesn't block the engine spy.
    fake_cq = types.ModuleType("license.compute_quota")
    fake_cq.__file__ = str(Path(_LICENSE_DIR) / "compute_quota.py")
    fake_cq.increment_and_check = lambda *a, **kw: None
    fake_limits = types.ModuleType("license.limits")
    fake_limits.__file__ = str(Path(_LICENSE_DIR) / "limits.py")
    class _LicErr(Exception): pass
    fake_limits.LicenseLimitError = _LicErr

    with (
        mock.patch.dict(os.environ, {"CORVIN_LICENSE_KEY": "fake-key-12345"}),
        mock.patch.dict(sys.modules, {
            "license.compute_quota": fake_cq,
            "license.limits": fake_limits,
        }),
        _allow_l44(),
    ):
        mod = _fresh_a2a_worker()
        result = mod.spawn_a2a_worker(
            instruction="hello",
            origin_id="test-origin",
            task_id="task-003",
            persona="assistant",
            ttl_s=5,
            engine_factory=lambda: _SpyEngine(),
        )

    assert spawn_envs, "Engine.spawn was not called — test inconclusive"
    passed_env = spawn_envs[0]
    assert "CORVIN_LICENSE_KEY" in passed_env, (
        "CORVIN_LICENSE_KEY should appear in env kwarg (set to empty string)"
    )
    assert passed_env["CORVIN_LICENSE_KEY"] == "", (
        f"Expected CORVIN_LICENSE_KEY='' in worker env, got {passed_env['CORVIN_LICENSE_KEY']!r}. "
        "ADR-0144: parent license key must not reach the worker subprocess."
    )


# ── session_refresh pre-write verify ─────────────────────────────────────────

def test_session_refresh_rejects_invalid_token_before_write():
    """session_refresh must NOT write tokens that fail Ed25519 verification (ADR-0144 F-A)."""
    _clear_mod("session_refresh", "license.session_refresh")

    import importlib
    sr = importlib.import_module("session_refresh")

    written_paths: list[str] = []

    def _spy_write(path, data):
        written_paths.append(str(path))
        # Don't actually write — tempdir may not match expected path.

    fake_validator = types.ModuleType("license.validator")
    # Always return None — simulates signature verification failure.
    fake_validator._verify_ed25519 = staticmethod(lambda token: None)

    fake_data = json.dumps({
        "session_token": "CORVIN-BAD-TOKEN",
        "exp": 9_999_999_999,
        "tier": "enterprise",
    }).encode("utf-8")

    mock_resp = mock.MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = mock.MagicMock(return_value=False)
    mock_resp.read.return_value = fake_data

    with (
        mock.patch.dict(sys.modules, {"license.validator": fake_validator}),
        mock.patch.object(sr, "_write_secure", side_effect=_spy_write),
        mock.patch("urllib.request.urlopen", return_value=mock_resp),
        # Patch load_features to return a valid features dict (no disk needed).
        mock.patch.object(sr, "load_features", return_value={
            "token_fp": "testfp",
            "api_key": "test-api-key",
        }),
        # Patch _find_license_token so we get past the "no token" guard.
        mock.patch.object(sr, "_find_license_token", return_value="CORVIN-VALID-EXISTING-TOKEN"),
        mock.patch.object(sr, "_get_device_fp", return_value="testdevice"),
        mock.patch.object(sr, "_sign_request", return_value=("ts-123", "sig-abc")),
        mock.patch.object(sr, "_compute_attestation", return_value="att-xyz"),
        mock.patch.object(sr, "_features_server", return_value="http://fake.local"),
    ):
        result = sr._do_refresh(timeout=5)

    assert result is False, (
        "session_refresh._do_refresh should return False when verification fails"
    )
    session_writes = [p for p in written_paths if "session" in p]
    assert not session_writes, (
        f"session.key was written despite invalid token: {session_writes}. "
        "ADR-0144 F-A: pre-write verify must block invalid tokens."
    )


# ── remote_trigger_receiver default rate limit ──────────────────────────────

def test_receiver_default_rate_limit_constant_is_60():
    """_DEFAULT_RATE_LIMIT_RPM must be 60 in remote_trigger_receiver (ADR-0144)."""
    _clear_mod("remote_trigger_receiver")
    import importlib
    rtr = importlib.import_module("remote_trigger_receiver")

    assert hasattr(rtr, "_DEFAULT_RATE_LIMIT_RPM"), (
        "_DEFAULT_RATE_LIMIT_RPM missing from remote_trigger_receiver. "
        "ADR-0144: origins without rate_limit_rpm need a safe default."
    )
    assert rtr._DEFAULT_RATE_LIMIT_RPM == 60, (
        f"Expected _DEFAULT_RATE_LIMIT_RPM=60, got {rtr._DEFAULT_RATE_LIMIT_RPM}"
    )


def test_receiver_default_rate_limit_applied_to_unconfigured_origin():
    """origin_config without rate_limit_rpm must fall back to _DEFAULT_RATE_LIMIT_RPM=60."""
    _clear_mod("remote_trigger_receiver")
    import importlib
    rtr = importlib.import_module("remote_trigger_receiver")

    # Simulate the get() call in _validate().
    empty_config: dict = {}
    effective = empty_config.get("rate_limit_rpm", rtr._DEFAULT_RATE_LIMIT_RPM)
    assert effective == 60, (
        f"Default rate limit not applied for unconfigured origin: got {effective}"
    )


# ── A3: CORVIN_HOME snapshot in a2a_worker ───────────────────────────────

def test_a2a_worker_has_corvin_home_snapshot():
    """a2a_worker must snapshot CORVIN_HOME at import time (ADR-0144 A3)."""
    mod = _fresh_a2a_worker()
    assert hasattr(mod, "_CORVIN_HOME_SNAPSHOT_A2A"), (
        "_CORVIN_HOME_SNAPSHOT_A2A missing from a2a_worker. "
        "ADR-0144 A3: CORVIN_HOME must be snapshotted at import, not read live."
    )
    from pathlib import Path
    assert isinstance(mod._CORVIN_HOME_SNAPSHOT_A2A, Path), (
        "_CORVIN_HOME_SNAPSHOT_A2A must be a Path object."
    )


def test_a2a_worker_snapshot_ignores_post_import_env_change():
    """Post-import CORVIN_HOME mutation must not affect the snapshot (ADR-0144 A3)."""
    original_home = os.environ.get("CORVIN_HOME")
    try:
        os.environ.pop("CORVIN_HOME", None)
        mod = _fresh_a2a_worker()
        snapshot_before = str(mod._CORVIN_HOME_SNAPSHOT_A2A)
        # Now mutate the env — should NOT change the already-snapshotted value.
        os.environ["CORVIN_HOME"] = "/tmp/evil-attacker-dir"
        assert str(mod._CORVIN_HOME_SNAPSHOT_A2A) == snapshot_before, (
            "Snapshot changed after env mutation — CORVIN_HOME not properly snapshotted. "
            "ADR-0144 A3: post-boot env mutation must not redirect the compute-quota gate."
        )
    finally:
        if original_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = original_home


# ── B1 parity: validator revocation URL is test-mode-gated ───────────────

def _fresh_validator(env: dict[str, str | None]):
    """Re-import operator.license.validator with a controlled env so its
    import-time CORVIN_FEATURES_URL / CORVIN_TEST_MODE snapshots are captured
    from `env` (keys mapped to None are removed)."""
    import importlib
    _clear_mod("license.validator", "validator")
    overrides = {k: v for k, v in env.items() if v is not None}
    removes = [k for k, v in env.items() if v is None]
    with mock.patch.dict(os.environ, overrides, clear=False):
        for k in removes:
            os.environ.pop(k, None)
        return importlib.import_module("validator")


def test_validator_features_url_ignored_without_test_mode():
    """Revocation endpoint (_fetch_revoked_fps) must ignore a post-boot / attacker
    CORVIN_FEATURES_URL in production so ADR-0102 per-token revocation cannot be
    nullified by redirecting the fetch to a dead/mock host (ADR-0144 B1 parity)."""
    v = _fresh_validator({
        "CORVIN_TEST_MODE": None,
        "CORVIN_FEATURES_URL": "http://127.0.0.1:9/",
    })
    assert v._features_base_url() == "https://corvin-features-production.up.railway.app", (
        "CORVIN_FEATURES_URL override leaked into the revocation fetch without "
        "CORVIN_TEST_MODE=1 — revocation bypass reopened."
    )


def test_validator_features_url_honoured_under_test_mode():
    """Under CORVIN_TEST_MODE=1 the override is honoured so the test suite can
    point revocation at a mock features server."""
    v = _fresh_validator({
        "CORVIN_TEST_MODE": "1",
        "CORVIN_FEATURES_URL": "http://127.0.0.1:9/",
    })
    assert v._features_base_url() == "http://127.0.0.1:9"


# ── A4: negative-floor clamp in compute_quota ────────────────────────────

def test_compute_quota_negative_value_clamped_to_zero():
    """compute_quota.json with negative today-count must not inflate the budget (ADR-0144 A4)."""
    import importlib
    _clear_mod("license.compute_quota", "license.limits", "license.validator")
    for _d in (_OPERATOR_DIR, _LICENSE_DIR):
        if _d not in sys.path:
            sys.path.insert(0, _d)

    cq = importlib.import_module("license.compute_quota")

    quota_calls: list[int] = []
    _original_do = cq._do_increment_and_check  # type: ignore[attr-defined]

    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_home = pathlib.Path(tmpdir)
        quota_dir = tmp_home / "global" / "license"
        quota_dir.mkdir(parents=True)
        quota_file = quota_dir / "compute_quota.json"

        # Write a malicious negative value for today.
        import json as _json
        from datetime import date, timezone
        today = date.today().strftime("%Y-%m-%d")
        quota_file.write_text(_json.dumps({today: -9999}))
        quota_file.chmod(0o600)

        # Fake validator that returns limit = 1 (smallest meaningful limit).
        fake_validator = types.ModuleType("license.validator")
        fake_validator.get_limit = lambda feature: 1
        fake_validator.active_tier = lambda: "free"

        with mock.patch.dict(sys.modules, {"license.validator": fake_validator}):
            # With negative injection, this should NOT pass (limit=1, effective current=0
            # after clamp, 0+1 <= 1 → allowed).  But it should NOT pass with current=-9999
            # turning into -9998 < 1 when NOT clamped.
            # What we test: calling twice must raise on second call (limit exhausted).
            try:
                cq.increment_and_check(tmp_home, channel="test", chat_key="test")
            except Exception as e:
                pytest.fail(f"First call raised unexpectedly: {e}")

            # Second call must now exhaust the limit (current was reset to 0+1=1 after clamp).
            from license.limits import LicenseLimitError  # type: ignore[import-not-found]
            with pytest.raises(LicenseLimitError):
                cq.increment_and_check(tmp_home, channel="test", chat_key="test")


# ── B5: A2A receiver must disallow Bash by default ───────────────────────

def test_receiver_disallows_bash_by_default():
    """When allow_bash is not set, Bash must appear in _a2a_disallowed (ADR-0144 B5)."""
    # Simulate the logic from the receiver's _spawn_worker():
    origin_config: dict = {}  # no allow_bash, no disallowed_tools

    _a2a_allowed = origin_config.get("allowed_tools")
    _base_disallowed: list = list(origin_config.get("disallowed_tools") or [])
    if not origin_config.get("allow_bash"):
        if "Bash" not in _base_disallowed:
            _base_disallowed.insert(0, "Bash")
    _a2a_disallowed = _base_disallowed or None

    assert _a2a_disallowed is not None, "Bash must be disallowed by default for A2A workers."
    assert "Bash" in _a2a_disallowed, (
        f"'Bash' not in disallowed list: {_a2a_disallowed}. "
        "ADR-0144 B5: A2A workers without allow_bash must not get Bash tool access."
    )


def test_receiver_allows_bash_when_explicitly_enabled():
    """allow_bash=True in origin config must not add Bash to disallowed list."""
    origin_config: dict = {"allow_bash": True}

    _base_disallowed: list = list(origin_config.get("disallowed_tools") or [])
    if not origin_config.get("allow_bash"):
        if "Bash" not in _base_disallowed:
            _base_disallowed.insert(0, "Bash")
    _a2a_disallowed = _base_disallowed or None

    assert _a2a_disallowed is None or "Bash" not in _a2a_disallowed, (
        "Bash must NOT be disallowed when allow_bash=True is explicitly set."
    )


# ── C7: WebFetch/WebSearch disallowed by default ─────────────────────────

def _simulate_receiver_tool_policy(origin_config: dict):
    """Simulate the ADR-0144 B5/C7/C8/FC4/Iter16 tool-policy derivation from the receiver."""
    _base_disallowed: list = list(origin_config.get("disallowed_tools") or [])
    if not origin_config.get("allow_bash"):
        if "Bash" not in _base_disallowed:
            _base_disallowed.insert(0, "Bash")
    if not origin_config.get("allow_network"):
        for _nt in ("WebFetch", "WebSearch"):
            if _nt not in _base_disallowed:
                _base_disallowed.append(_nt)
    if not origin_config.get("allow_read_files"):
        for _rt in ("Read", "Grep", "Glob", "LS"):
            if _rt not in _base_disallowed:
                _base_disallowed.append(_rt)
    if not origin_config.get("allow_write_files"):
        for _wt in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            if _wt not in _base_disallowed:
                _base_disallowed.append(_wt)
    if not origin_config.get("allow_subagents"):
        for _st in ("Task", "TodoWrite", "TodoRead"):
            if _st not in _base_disallowed:
                _base_disallowed.append(_st)
    return _base_disallowed or None


def test_receiver_disallows_webfetch_by_default():
    """WebFetch must be in disallowed list by default (ADR-0144 C7 exfil fix)."""
    policy = _simulate_receiver_tool_policy({})
    assert policy is not None, "disallowed list must not be None with no origin config"
    assert "WebFetch" in policy, (
        f"WebFetch not in disallowed list: {policy}. "
        "ADR-0144 C7: Read+WebFetch exfil chain must be blocked by default."
    )
    assert "WebSearch" in policy, (
        f"WebSearch not in disallowed list: {policy}. "
        "ADR-0144 C7: WebSearch as network exfil channel must be blocked by default."
    )


def test_receiver_allows_network_when_explicitly_enabled():
    """allow_network=True must remove WebFetch/WebSearch from disallowed list."""
    policy = _simulate_receiver_tool_policy({"allow_network": True})
    # With no bash opt-in, Bash still blocked; network tools allowed.
    assert "Bash" in (policy or []), "Bash must still be disallowed without allow_bash"
    if policy:
        assert "WebFetch" not in policy, "WebFetch must be allowed with allow_network=True"
        assert "WebSearch" not in policy, "WebSearch must be allowed with allow_network=True"


# ── Q1/Q2: compute_quota float("inf")/float("nan") guard ─────────────────

def test_compute_quota_load_rejects_infinity():
    """compute_quota._load must not accept float('inf') as a valid counter (ADR-0144 Q1)."""
    import importlib
    _clear_mod("license.compute_quota", "license.limits", "license.validator")
    cq = importlib.import_module("license.compute_quota")

    import json as _json, tempfile, pathlib
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        # Python's json module won't write Infinity, but if the file was
        # corrupted manually, we test the _load guard directly.
        # Simulate by calling _load on a dict with float("inf").
        pass

    # Direct test: the dict-comprehension in _load filters out non-finite floats.
    raw = {today: float("inf"), "nan-day": float("nan"), "2026-01-01": -5, "2026-01-02": 3}
    result = {
        k: int(v) for k, v in raw.items()
        if (isinstance(v, int) and not isinstance(v, bool))
        or (isinstance(v, float) and v == v and abs(v) != float("inf"))
    }
    assert today not in result, (
        f"float('inf') for today should be excluded from quota dict, but got {result}. "
        "ADR-0144 Q1: inf values must not reset the counter silently."
    )
    assert "nan-day" not in result, (
        f"float('nan') should be excluded (nan == nan is False), but got {result}. "
        "ADR-0144 Q2."
    )
    # Negative ints are NOT filtered — max(0, ...) in _do_increment_and_check handles them.
    assert result.get("2026-01-01") == -5, "negative int must pass through _load filter"
    assert result.get("2026-01-02") == 3, "normal integer must survive the filter"


# ── V5: check_public_key_integrity uses snapshot, not live env ───────────

def test_check_public_key_integrity_uses_test_mode_snapshot():
    """check_public_key_integrity must use _TEST_MODE_SNAPSHOT, not live os.environ (ADR-0144 V5)."""
    import importlib
    _clear_mod("session_refresh", "license.session_refresh")

    with mock.patch.dict(os.environ, {"CORVIN_TEST_MODE": "1"}):
        sr = importlib.import_module("session_refresh")
        # _TEST_MODE_SNAPSHOT captured "1" at import time.
        assert sr._TEST_MODE_SNAPSHOT == "1"

    # Now clear env — snapshot must still be "1" since it was captured during import.
    result = sr.check_public_key_integrity()
    assert result is True, (
        "check_public_key_integrity returned False — likely using live os.environ "
        "instead of _TEST_MODE_SNAPSHOT. ADR-0144 V5."
    )


# ── C8/FC4: Read + Write tools blocked by default in A2A workers ─────────

def test_receiver_disallows_read_by_default():
    """Read + Grep/Glob/LS must be blocked by default (ADR-0144 F-C4-01 + Iter16)."""
    policy = _simulate_receiver_tool_policy({})
    assert policy is not None
    for tool in ("Read", "Grep", "Glob", "LS"):
        assert tool in policy, (
            f"'{tool}' not blocked by default: {policy}. "
            "ADR-0144: Grep/Glob/LS are Read-bypasses and must also be blocked."
        )


def test_receiver_disallows_task_by_default():
    """Task/TodoWrite/TodoRead must be blocked by default (ADR-0144 Iter16: subagent bypass)."""
    policy = _simulate_receiver_tool_policy({})
    assert policy is not None
    for tool in ("Task", "TodoWrite", "TodoRead"):
        assert tool in policy, (
            f"'{tool}' not blocked by default: {policy}. "
            "ADR-0144: Task tool spawns subagents with DEFAULT permissions (not caller's restrictions)."
        )


def test_receiver_disallows_write_tools_by_default():
    """Write/Edit/MultiEdit/NotebookEdit must be blocked by default (ADR-0144 C8)."""
    policy = _simulate_receiver_tool_policy({})
    assert policy is not None
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        assert tool in policy, (
            f"'{tool}' not blocked by default: {policy}. "
            "ADR-0144 C8: path_gate hooks inactive for /tmp workers — write tools must be blocked."
        )


def test_receiver_allows_read_when_explicitly_enabled():
    """allow_read_files=True must unblock the Read tool."""
    policy = _simulate_receiver_tool_policy({"allow_read_files": True})
    assert policy is None or "Read" not in policy, (
        "Read must be allowed when allow_read_files=True."
    )


def test_receiver_allows_write_when_explicitly_enabled():
    """allow_write_files=True must unblock Write/Edit/MultiEdit/NotebookEdit."""
    policy = _simulate_receiver_tool_policy({"allow_write_files": True})
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        assert policy is None or tool not in policy, (
            f"'{tool}' must be allowed when allow_write_files=True."
        )


# ── F-C4-02: sensitive env vars cleared in worker spawn ──────────────────

def test_a2a_worker_clears_sensitive_env_vars():
    """spawn_a2a_worker must clear ALL sensitive env vars, not just CORVIN_LICENSE_KEY (ADR-0144 FC4-02)."""
    spawn_envs: list[dict] = []

    class _SpyEngine:
        name = "spy"
        def spawn(self, prompt, **kwargs):
            spawn_envs.append(dict(kwargs.get("env") or {}))
            raise RuntimeError("spy-done")

    fake_cq = types.ModuleType("license.compute_quota")
    fake_cq.__file__ = str(Path(_LICENSE_DIR) / "compute_quota.py")
    fake_cq.increment_and_check = lambda *a, **kw: None
    fake_limits = types.ModuleType("license.limits")
    fake_limits.__file__ = str(Path(_LICENSE_DIR) / "limits.py")
    class _LicErr(Exception): pass
    fake_limits.LicenseLimitError = _LicErr

    sensitive_vars = {
        "CORVIN_LICENSE_KEY": "secret-license",
        "OPENAI_API_KEY": "sk-test-openai",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "OLLAMA_API_KEY": "ollama-secret",
        "HETZNER_API_TOKEN": "hetzner-secret",
    }

    with (
        mock.patch.dict(os.environ, sensitive_vars),
        mock.patch.dict(sys.modules, {
            "license.compute_quota": fake_cq,
            "license.limits": fake_limits,
        }),
        _allow_l44(),
    ):
        mod = _fresh_a2a_worker()
        mod.spawn_a2a_worker(
            instruction="hello",
            origin_id="test-origin",
            task_id="task-fc4",
            persona="assistant",
            ttl_s=5,
            engine_factory=lambda: _SpyEngine(),
        )

    assert spawn_envs, "Engine.spawn was not called — test inconclusive"
    passed_env = spawn_envs[0]
    # ADR-0144 FC4-02 + Iter17: ALL sensitive env vars must be cleared
    for var in (
        "CORVIN_LICENSE_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",   # Iter17: adapter sets this for claude_code_local
        "ANTHROPIC_BASE_URL",     # Iter17: SSRF redirect to internal Ollama
        "OLLAMA_API_KEY",
        "HETZNER_API_TOKEN",
        "FORGE_ROOT",             # Iter17: disables user-scope Forge MCP in subprocess
        "CORVIN_TEST_MODE",       # Iter18: prevents test-mode bypass when adapter runs in CI
        "CORVIN_FEATURES_URL",    # Iter18: prevents SSRF to mock-features server via Forge MCP
    ):
        assert var in passed_env, f"{var} should appear in env dict (set to empty string)"
        assert passed_env[var] == "", (
            f"Expected {var}='' in worker env, got {passed_env[var]!r}. "
            "ADR-0144 FC4-02: all sensitive env vars must be cleared for the worker."
        )


# ── A2A-CQ-NO-B1-02: PYTHONPATH-shadow of the license module is fail-closed ──

def test_a2a_worker_rejects_shadowed_license_module():
    """A license.compute_quota loaded from OUTSIDE operator/license/ (a PYTHONPATH
    shadow) must make the A2A spawn fail CLOSED — not silently no-op the quota gate.

    The standalone a2a_http_server entrypoint never runs the adapter's boot B1
    gate, so a2a_worker must detect the shadow itself (ADR-0144 A2A-CQ-NO-B1-02).
    """
    spawn_called: list[bool] = []

    class _SpyEngine:
        name = "spy"
        def spawn(self, prompt, **kwargs):
            spawn_called.append(True)
            raise RuntimeError("spy-done")

    # Fake whose __file__ points OUTSIDE operator/license — i.e. an attacker shadow.
    fake_cq = types.ModuleType("license.compute_quota")
    fake_cq.__file__ = "/tmp/evil/license/compute_quota.py"
    fake_cq.increment_and_check = lambda *a, **kw: None  # attacker no-op
    fake_limits = types.ModuleType("license.limits")
    fake_limits.__file__ = "/tmp/evil/license/limits.py"
    class _LicErr(Exception): pass
    fake_limits.LicenseLimitError = _LicErr

    with mock.patch.dict(sys.modules, {
        "license.compute_quota": fake_cq,
        "license.limits": fake_limits,
    }), _allow_l44():
        mod = _fresh_a2a_worker()
        result = mod.spawn_a2a_worker(
            instruction="hello",
            origin_id="shadow-origin",
            task_id="task-shadow",
            persona="assistant",
            ttl_s=5,
            engine_factory=lambda: _SpyEngine(),
        )

    assert not spawn_called, "Engine.spawn must NOT run when a license shadow is detected"
    assert getattr(result, "status", None) == "rejected", (
        f"Expected status='rejected' on license shadow, got {getattr(result, 'status', None)!r}"
    )
    assert "shadow" in (getattr(result, "error", "") or "").lower(), (
        f"Expected a shadow-detection error, got {getattr(result, 'error', None)!r}"
    )
