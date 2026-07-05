"""Tests for HealingTrace (ADR-0180) — M1 + M2 acceptance criteria.

Verifies:
  - _assert_safe_htrace drops unknown fields (allow-list)
  - _assert_safe_htrace drops PII-bearing values
  - HealingTrace.from_heal_event normalises correctly
  - write_trace respects consent gate and size cap
  - ConsentAct round-trip
  - Fingerprint stability
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corvin_console.aco.htrace import (
    HealingTrace,
    _assert_safe_htrace,
    _safe_template,
    make_fingerprint,
    config_profile_hash,
    write_trace,
    _inc_dropped,
    compress_for_upload,
    htrace_dir,
    _today_utc,
)
from corvin_console.aco.htrace_allowlists import (
    HTRACE_FIELD_ALLOWLIST,
    NS_ALLOWLIST,
    EVENT_SEQ_ALLOWLIST,
)
from corvin_console.aco.htrace_consent import (
    ConsentAct,
    healing_traces_enabled,
    _consent_act_path,
    load_consent_act_id,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def minimal_record():
    return {
        "schema": "htrace/1",
        "corvin_version": "0.9.60",
        "platform": "linux/x86_64",
        "python": "3.12",
        "error_fingerprint": "a" * 64,
        "error_type": "AttributeError",
        "error_module_ns": "chat_runtime",
        "error_function": "stream_turn",
        "error_line": 42,
        "error_template": "object '{}' has no attribute '{}'",
        "stack_frames": [{"ns": "chat_runtime", "fn": "stream_turn", "ln": 42}],
        "event_sequence": ["os_turn.started", "heal.triggered"],
        "heal_action": "restart_service",
        "heal_outcome": "success",
        "config_profile_hash": "b" * 64,
        "tenant_shape": "single",
        "ts_day": "2026-07-03",
        "consent_act_id": "cid-" + "x" * 36,
        "instance_token": "tok-" + "x" * 36,
    }


# ── _assert_safe_htrace — allow-list enforcement ─────────────────────────────

class TestAssertSafeHtrace:
    def test_valid_record_passes(self, minimal_record):
        _assert_safe_htrace(minimal_record)  # must not raise

    def test_unknown_field_raises(self, minimal_record):
        minimal_record["surprise_field"] = "oops"
        with pytest.raises(ValueError, match="unknown fields"):
            _assert_safe_htrace(minimal_record)

    def test_email_in_error_type_raises(self, minimal_record):
        minimal_record["error_type"] = "user@example.com is missing"
        with pytest.raises(ValueError, match="PII"):
            _assert_safe_htrace(minimal_record)

    def test_home_path_in_template_raises(self, minimal_record):
        minimal_record["error_template"] = "file /home/silvio/secret not found"
        with pytest.raises(ValueError, match="PII"):
            _assert_safe_htrace(minimal_record)

    def test_ipv4_in_heal_action_raises(self, minimal_record):
        minimal_record["heal_action"] = "connect to 192.168.1.100"
        with pytest.raises(ValueError, match="PII"):
            _assert_safe_htrace(minimal_record)

    def test_long_hex_token_raises(self, minimal_record):
        minimal_record["error_template"] = "token " + "a" * 20
        with pytest.raises(ValueError, match="PII"):
            _assert_safe_htrace(minimal_record)

    def test_unsafe_stack_frame_ns_raises(self, minimal_record):
        minimal_record["stack_frames"] = [{"ns": "customer_plugin.billing", "fn": "charge", "ln": 1}]
        with pytest.raises(ValueError, match="unsafe stack frame ns"):
            _assert_safe_htrace(minimal_record)

    def test_external_frame_is_allowed(self, minimal_record):
        minimal_record["stack_frames"] = [{"ns": "[external]", "fn": "[redacted]", "ln": 0}]
        _assert_safe_htrace(minimal_record)  # must not raise

    def test_unsafe_event_name_raises(self, minimal_record):
        minimal_record["event_sequence"] = ["security.privilege_escalation.detected"]
        with pytest.raises(ValueError, match="unsafe event name"):
            _assert_safe_htrace(minimal_record)

    def test_all_allowlisted_fields_are_accepted(self, minimal_record):
        assert set(minimal_record.keys()) == HTRACE_FIELD_ALLOWLIST

    def test_pii_in_corvin_version_raises(self, minimal_record):
        """corvin_version is now scanned — PII injected via direct field set is caught."""
        minimal_record["corvin_version"] = "user@example.com"
        with pytest.raises(ValueError, match="PII"):
            _assert_safe_htrace(minimal_record)

    def test_extra_stack_frame_key_raises(self, minimal_record):
        """Extra keys in stack frames bypass the PII scanner — must be rejected."""
        minimal_record["stack_frames"] = [
            {"ns": "chat_runtime", "fn": "send", "ln": 1, "user": "alice@example.com"}
        ]
        with pytest.raises(ValueError, match="unknown stack frame keys"):
            _assert_safe_htrace(minimal_record)


# ── PII injection attempts ────────────────────────────────────────────────────

class TestPIIInjection:
    """Verify that common PII shapes are all caught by _assert_safe_htrace."""

    @pytest.mark.parametrize("pii_value", [
        "user@example.com",
        "/home/user/secret.txt",
        "C:\\Users\\silvio\\Desktop\\file.txt",
        "192.168.0.1",
        "ghp_AAAAAAAAAAAA1234567890",
        "sk_live_ABCDEFGHIJKLMNOPQRST",
        "token:" + "a" * 20,
        "~/Documents/important",
    ])
    def test_pii_in_error_template_is_caught(self, minimal_record, pii_value):
        minimal_record["error_template"] = pii_value
        with pytest.raises(ValueError):
            _assert_safe_htrace(minimal_record)


# ── _safe_template ────────────────────────────────────────────────────────────

class TestSafeTemplate:
    def test_replaces_quoted_strings(self):
        t = _safe_template("object 'my_var' has no attribute 'foo'")
        assert "my_var" not in t
        assert "{}" in t

    def test_replaces_paths(self):
        t = _safe_template("cannot open /home/silvio/file.txt")
        assert "/home/silvio" not in t
        assert "[path]" in t

    def test_keeps_structural_words(self):
        t = _safe_template("expected int, got str at position 3")
        assert "expected" in t or "int" in t  # safe structural words preserved

    def test_caps_length(self):
        t = _safe_template("x" * 1000)
        assert len(t) <= 200

    def test_pii_falls_back_to_redacted(self):
        t = _safe_template("error at user@example.com address")
        assert t == "[message.redacted]"


# ── Fingerprint ───────────────────────────────────────────────────────────────

class TestFingerprint:
    def test_stable_across_calls(self):
        fp1 = make_fingerprint("AttributeError", "chat_runtime", "stream_turn")
        fp2 = make_fingerprint("AttributeError", "chat_runtime", "stream_turn")
        assert fp1 == fp2

    def test_normalisation_strips_package_prefix(self):
        fp1 = make_fingerprint("AttributeError", "corvin_console.chat_runtime", "stream_turn")
        fp2 = make_fingerprint("AttributeError", "chat_runtime", "stream_turn")
        assert fp1 == fp2

    def test_different_exc_types_differ(self):
        fp1 = make_fingerprint("AttributeError", "chat_runtime", "stream_turn")
        fp2 = make_fingerprint("KeyError", "chat_runtime", "stream_turn")
        assert fp1 != fp2

    def test_is_64_hex_chars(self):
        fp = make_fingerprint("ValueError", "audit", "write_event")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


# ── config_profile_hash ───────────────────────────────────────────────────────

class TestConfigProfileHash:
    def test_values_not_included(self):
        h1 = config_profile_hash({"engine": "claude", "model": "opus"})
        h2 = config_profile_hash({"engine": "hermes", "model": "qwen"})
        assert h1 == h2  # same keys, different values → same hash

    def test_unknown_keys_excluded(self):
        h1 = config_profile_hash({"engine": "claude"})
        h2 = config_profile_hash({"engine": "claude", "customer_secret": "abc"})
        assert h1 == h2  # unknown key excluded

    def test_different_keys_differ(self):
        h1 = config_profile_hash({"engine": "claude", "forge_enabled": "true"})
        h2 = config_profile_hash({"engine": "claude"})
        assert h1 != h2


# ── HealingTrace.from_heal_event ──────────────────────────────────────────────

class TestFromHealEvent:
    def test_builds_from_exception(self):
        try:
            raise AttributeError("object 'foo' has no attribute 'bar'")
        except AttributeError as e:
            trace = HealingTrace.from_heal_event(
                e,
                event_sequence=["os_turn.started", "heal.triggered"],
                heal_action="restart_service",
                heal_outcome="success",
            )
        assert trace.error_type == "AttributeError"
        assert trace.heal_action == "restart_service"
        assert trace.heal_outcome == "success"
        assert len(trace.error_fingerprint) == 64

    def test_multi_tenant_not_blocked_here(self):
        """tenant_shape is a field; blocking happens in write_trace."""
        try:
            raise ValueError("test")
        except ValueError as e:
            trace = HealingTrace.from_heal_event(
                e,
                event_sequence=[],
                heal_action="",
                heal_outcome="skipped",
                tenant_shape="multi",
            )
        assert trace.tenant_shape == "multi"

    def test_unsafe_event_names_redacted(self):
        try:
            raise RuntimeError("x")
        except RuntimeError as e:
            trace = HealingTrace.from_heal_event(
                e,
                event_sequence=["security.privilege_escalation.detected", "os_turn.started"],
                heal_action="",
                heal_outcome="skipped",
            )
        assert "[event.redacted]" in trace.event_sequence
        assert "security.privilege_escalation.detected" not in trace.event_sequence

    def test_validated_passes_assert_safe(self):
        try:
            raise TypeError("bad type")
        except TypeError as e:
            trace = HealingTrace.from_heal_event(
                e,
                event_sequence=["os_turn.started"],
                heal_action="cache_clear",
                heal_outcome="success",
                consent_act_id="test-act-id",
                instance_token="test-token",
            )
        record = trace.validated()  # must not raise
        assert set(record.keys()).issubset(HTRACE_FIELD_ALLOWLIST)


# ── write_trace ───────────────────────────────────────────────────────────────

class TestWriteTrace:
    def test_no_consent_does_not_write(self, tmpdir):
        try:
            raise ValueError("x")
        except ValueError as e:
            trace = HealingTrace.from_heal_event(e, event_sequence=[], heal_action="", heal_outcome="skipped")
        result = write_trace(trace, tmpdir, consent_active=False)
        assert result is False
        assert not (htrace_dir(tmpdir)).exists()

    def test_with_consent_writes_jsonl(self, tmpdir):
        try:
            raise ValueError("test write")
        except ValueError as e:
            trace = HealingTrace.from_heal_event(e, event_sequence=["os_turn.started"],
                                                  heal_action="cache_clear", heal_outcome="success")
        result = write_trace(trace, tmpdir, consent_active=True)
        assert result is True
        today = htrace_dir(tmpdir) / f"{_today_utc()}.jsonl"
        assert today.exists()
        records = [json.loads(ln) for ln in today.read_text().splitlines() if ln.strip()]
        assert len(records) == 1
        assert records[0]["error_type"] == "ValueError"

    def test_multi_tenant_is_blocked(self, tmpdir):
        try:
            raise ValueError("x")
        except ValueError as e:
            trace = HealingTrace.from_heal_event(e, event_sequence=[], heal_action="", heal_outcome="skipped",
                                                  tenant_shape="multi")
        result = write_trace(trace, tmpdir, consent_active=True)
        assert result is False

    def test_invalid_record_increments_dropped(self, tmpdir):
        trace = HealingTrace()
        trace.error_template = "leak: user@example.com"  # will fail _assert_safe_htrace
        result = write_trace(trace, tmpdir, consent_active=True)
        assert result is False
        dropped_p = htrace_dir(tmpdir) / "dropped.count"
        assert dropped_p.exists()
        assert int(dropped_p.read_text().strip()) == 1


# ── compress_for_upload ───────────────────────────────────────────────────────

class TestCompressForUpload:
    def test_compresses_jsonl_to_gz(self, tmpdir):
        d = htrace_dir(tmpdir)
        d.mkdir(parents=True)
        jsonl = d / "2026-07-02.jsonl"
        jsonl.write_text('{"schema":"htrace/1"}\n', encoding="utf-8")
        result = compress_for_upload(tmpdir, date_str="2026-07-02")
        assert result is not None
        assert result.suffix == ".gz"
        assert not jsonl.exists()
        # Verify it's valid gzip
        with gzip.open(result, "rt") as fh:
            data = fh.read()
        assert "htrace/1" in data

    def test_returns_none_when_no_file(self, tmpdir):
        result = compress_for_upload(tmpdir, date_str="2000-01-01")
        assert result is None

    def test_invalid_date_str_raises(self, tmpdir):
        """Path traversal via date_str must be rejected."""
        with pytest.raises(ValueError, match="invalid date_str"):
            compress_for_upload(tmpdir, date_str="../../etc/passwd")

    def test_date_str_traversal_with_dotdot_raises(self, tmpdir):
        with pytest.raises(ValueError, match="invalid date_str"):
            compress_for_upload(tmpdir, date_str="2026-07-02/../../../etc")


# ── ConsentAct ───────────────────────────────────────────────────────────────

class TestConsentAct:
    def _make_act(self):
        return ConsentAct(
            consent_act_id=str(uuid.uuid4()),
            consent_version="htrace/1.0",
            ts_utc="2026-07-03T12:00:00Z",
            text_sha256="a" * 64,
            method="cli",
            corvin_version="0.9.60",
        )

    def test_round_trip(self, tmpdir):
        act = self._make_act()
        act.save(tmpdir)
        loaded = ConsentAct.load(tmpdir)
        assert loaded is not None
        assert loaded.consent_act_id == act.consent_act_id
        assert loaded.consent_version == act.consent_version

    def test_missing_file_returns_none(self, tmpdir):
        assert ConsentAct.load(tmpdir) is None

    def test_malformed_file_returns_none(self, tmpdir):
        _consent_act_path(tmpdir).parent.mkdir(parents=True, exist_ok=True)
        _consent_act_path(tmpdir).write_text("not-json", encoding="utf-8")
        assert ConsentAct.load(tmpdir) is None

    def test_wrong_version_detected(self, tmpdir):
        act = self._make_act()
        act.save(tmpdir)
        loaded = ConsentAct.load(tmpdir)
        assert loaded is not None
        # Corrupt the version
        loaded.consent_version = "htrace/99.0"
        assert not loaded.is_current_version

    def test_load_consent_act_id_returns_empty_when_absent(self, tmpdir):
        assert load_consent_act_id(tmpdir) == ""

    def test_load_consent_act_id_returns_id_when_present(self, tmpdir):
        act = self._make_act()
        act.save(tmpdir)
        cid = load_consent_act_id(tmpdir)
        assert cid == act.consent_act_id


# ── healing_traces_enabled (double gate) ─────────────────────────────────────

class TestHealingTracesEnabled:
    def _make_valid_act(self, tmpdir):
        """Store a valid ConsentAct with the current version."""
        from corvin_console.aco.htrace_consent import _CONSENT_VERSION, _consent_text_sha256
        act = ConsentAct(
            consent_act_id=str(uuid.uuid4()),
            consent_version=_CONSENT_VERSION,
            ts_utc="2026-07-03T12:00:00Z",
            text_sha256=_consent_text_sha256(),
            method="cli",
            corvin_version="0.9.60",
        )
        act.save(tmpdir)
        return act

    def test_disabled_without_yaml_flag(self, tmpdir):
        self._make_valid_act(tmpdir)
        cfg = {"telemetry": {"healing_traces": False}}
        assert healing_traces_enabled(tmpdir, cfg=cfg) is False

    def test_disabled_without_consent_act(self, tmpdir):
        cfg = {"telemetry": {"healing_traces": True}}
        assert healing_traces_enabled(tmpdir, cfg=cfg) is False

    def test_enabled_with_both_gates(self, tmpdir):
        self._make_valid_act(tmpdir)
        cfg = {"telemetry": {"healing_traces": True}}
        assert healing_traces_enabled(tmpdir, cfg=cfg) is True

    def test_disabled_on_version_mismatch(self, tmpdir):
        from corvin_console.aco.htrace_consent import _consent_text_sha256
        act = ConsentAct(
            consent_act_id=str(uuid.uuid4()),
            consent_version="htrace/0.9",   # wrong version
            ts_utc="2026-07-03T12:00:00Z",
            text_sha256=_consent_text_sha256(),
            method="cli",
            corvin_version="0.9.60",
        )
        act.save(tmpdir)
        cfg = {"telemetry": {"healing_traces": True}}
        assert healing_traces_enabled(tmpdir, cfg=cfg) is False

    def test_enabled_via_ondisk_yaml_no_cfg_arg(self, tmpdir, monkeypatch):
        """Regression (R1 Finding 1): with NO cfg= argument the YAML flag must be
        read from tenants/_default/global/tenant.corvin.yaml (ADR-0007 layout).

        The pre-fix path (home.parent.parent/"global") resolved to a directory
        that never exists, so the fallback always returned False and silently
        killed the whole telemetry pipeline.  Every other test in this class
        passes cfg= explicitly and bypasses this path entirely."""
        monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
        self._make_valid_act(tmpdir)
        cfg_dir = tmpdir / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "tenant.corvin.yaml").write_text(
            "telemetry:\n  healing_traces: true\n", encoding="utf-8"
        )
        # No cfg= → must resolve the real on-disk config path and return True.
        assert healing_traces_enabled(tmpdir) is True

    def test_disabled_via_ondisk_yaml_flag_false_no_cfg_arg(self, tmpdir, monkeypatch):
        """Deny-by-default: on-disk flag false (no cfg= arg) → gate stays closed."""
        monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
        self._make_valid_act(tmpdir)
        cfg_dir = tmpdir / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "tenant.corvin.yaml").write_text(
            "telemetry:\n  healing_traces: false\n", encoding="utf-8"
        )
        assert healing_traces_enabled(tmpdir) is False

    def test_disabled_when_ondisk_yaml_absent_no_cfg_arg(self, tmpdir, monkeypatch):
        """Deny-by-default: no config file at all (no cfg= arg) → gate stays closed."""
        monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
        self._make_valid_act(tmpdir)
        assert healing_traces_enabled(tmpdir) is False

    def test_disabled_on_text_hash_mismatch(self, tmpdir):
        """is_text_intact must block consent whose text_sha256 no longer matches the file."""
        from corvin_console.aco.htrace_consent import _CONSENT_VERSION
        act = ConsentAct(
            consent_act_id=str(uuid.uuid4()),
            consent_version=_CONSENT_VERSION,   # version is correct …
            ts_utc="2026-07-03T12:00:00Z",
            text_sha256="a" * 64,               # … but hash is wrong
            method="cli",
            corvin_version="0.9.60",
        )
        act.save(tmpdir)
        cfg = {"telemetry": {"healing_traces": True}}
        assert healing_traces_enabled(tmpdir, cfg=cfg) is False


# ── Fix regression tests (R1 findings) ────────────────────────────────────────

class TestR1Fixes:
    """Regression tests for findings fixed in review round 1."""

    def test_error_line_string_raises(self, minimal_record):
        """error_line must be int — string PII bypassed _scan_value before fix."""
        minimal_record["error_line"] = "user@example.com"
        with pytest.raises(ValueError, match="error_line must be int"):
            _assert_safe_htrace(minimal_record)

    def test_stack_frame_ln_string_raises(self, minimal_record):
        """stack_frames.ln must be int — string IP bypassed _scan_value before fix."""
        minimal_record["stack_frames"] = [{"ns": "chat_runtime", "fn": "f", "ln": "192.168.1.1"}]
        with pytest.raises(ValueError, match="stack frame ln must be int"):
            _assert_safe_htrace(minimal_record)

    def test_consent_text_sha256_is_pinned(self):
        """_consent_text_sha256() must return the pinned constant, not a live file hash."""
        from corvin_console.aco.htrace_consent import (
            _consent_text_sha256,
            _CONSENT_TEXT_SHA256_PINNED,
        )
        assert _consent_text_sha256() == _CONSENT_TEXT_SHA256_PINNED

    def test_pinned_hash_matches_shipped_file(self):
        """Pinned SHA-256 must match the actual consent text file (CI guard)."""
        import hashlib
        from corvin_console.aco.htrace_consent import (
            _consent_text,
            _CONSENT_TEXT_SHA256_PINNED,
        )
        actual = hashlib.sha256(_consent_text().encode("utf-8")).hexdigest()
        assert actual == _CONSENT_TEXT_SHA256_PINNED, (
            f"htrace-1.0.txt was modified without updating _CONSENT_TEXT_SHA256_PINNED. "
            f"New hash: {actual}"
        )

    def test_ns_allowlist_no_duplicates(self):
        """NS_ALLOWLIST frozenset must contain chat_runtime and have a stable minimum size."""
        from corvin_console.aco.htrace_allowlists import NS_ALLOWLIST
        assert "chat_runtime" in NS_ALLOWLIST
        assert len(NS_ALLOWLIST) >= 35

    def test_uploader_module_imports_without_fcntl(self):
        """htrace_uploader must be importable on platforms without fcntl (Windows)."""
        import sys
        import builtins
        mods_to_remove = [k for k in sys.modules if "htrace_uploader" in k]
        for m in mods_to_remove:
            del sys.modules[m]
        real_import = builtins.__import__
        def _no_fcntl(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("simulated Windows: no module named 'fcntl'")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = _no_fcntl
        try:
            import importlib
            import corvin_console.aco.htrace_uploader as uploader
            importlib.reload(uploader)
            assert uploader._HAS_FLOCK is False
        finally:
            builtins.__import__ = real_import
            for m in [k for k in sys.modules if "htrace_uploader" in k]:
                del sys.modules[m]


class TestPingDenyByDefault:
    """The activity ping (ADR-0180) must be OPT-IN — CLAUDE.md compliance
    red-line: telemetry may never be opt-out / default-on."""

    def _write_cfg(self, home: Path, value) -> None:
        import yaml
        p = (home / "tenants" / "_default" / "global" / "tenant.corvin.yaml")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump({"spec": {"telemetry": {"ping_enabled": value}}}),
                     encoding="utf-8")

    def test_fresh_install_ping_is_off(self, tmpdir):
        from corvin_console.aco.htrace_consent import ping_enabled
        # No ConsentAct, no config → deny-by-default.
        assert ping_enabled(tmpdir) is False

    def test_config_false_keeps_ping_off(self, tmpdir):
        from corvin_console.aco.htrace_consent import ping_enabled
        self._write_cfg(tmpdir, False)
        assert ping_enabled(tmpdir) is False

    def test_explicit_config_true_opts_in(self, tmpdir):
        from corvin_console.aco.htrace_consent import ping_enabled
        self._write_cfg(tmpdir, True)
        assert ping_enabled(tmpdir) is True

    def test_recorded_consent_opts_in(self, tmpdir):
        from corvin_console.aco.htrace_consent import ping_enabled, ConsentAct
        ConsentAct(
            consent_act_id="test-act",
            consent_version="htrace/1.1",
            ts_utc="2026-07-05T00:00:00Z",
            text_sha256="0" * 64,
            method="cli",
            corvin_version="0.0.0-test",
        ).save(tmpdir)
        assert ping_enabled(tmpdir) is True

    def test_non_bool_truthy_config_does_not_opt_in(self, tmpdir):
        # Only a literal `true` enables; a truthy-but-not-True value stays OFF
        # (fail-closed — no accidental opt-in via "yes"/1/etc.).
        from corvin_console.aco.htrace_consent import ping_enabled
        self._write_cfg(tmpdir, "yes")
        assert ping_enabled(tmpdir) is False
