"""Tests for model_selector.py — Layer 29.5 Phase 3 (ADR-0024).

All tests: stdlib only, no bridge imports, no anthropic SDK.
"""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the shared/ directory importable from any cwd.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import model_selector as ms  # noqa: E402


class EstimateTests(unittest.TestCase):
    """Phase 29.5.3a: estimate_os_turn_chars."""

    def test_sums_components(self) -> None:
        chars = ms.estimate_os_turn_chars(
            prompt="A" * 100,
            system_prompt="B" * 200,
            mcp_config_text="C" * 50,
            session_dir=None,
        )
        self.assertEqual(chars, 100 + 200 + 50 + ms._INTERNAL_OVERHEAD_CHARS)

    def test_session_dir_none_adds_zero(self) -> None:
        chars = ms.estimate_os_turn_chars("x", "y", session_dir=None)
        self.assertEqual(chars, 1 + 1 + ms._INTERNAL_OVERHEAD_CHARS)

    def test_session_dir_missing_adds_zero(self) -> None:
        chars = ms.estimate_os_turn_chars(
            "x", "y",
            session_dir=Path("/tmp/__nonexistent_adr0024__"),
        )
        self.assertEqual(chars, 1 + 1 + ms._INTERNAL_OVERHEAD_CHARS)

    def test_session_dir_counts_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.json").write_bytes(b"x" * 500)
            (p / "b.json").write_bytes(b"y" * 300)
            chars = ms.estimate_os_turn_chars("z", "", session_dir=p)
        self.assertEqual(chars, 1 + 0 + 800 + ms._INTERNAL_OVERHEAD_CHARS)

    def test_session_dir_capped_at_5mb(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            # Write 6 MB across two files → capped at 5 MB.
            big = b"x" * (3 * 1024 * 1024)
            (p / "a.bin").write_bytes(big)
            (p / "b.bin").write_bytes(big)
            chars = ms.estimate_os_turn_chars("", "", session_dir=p)
        self.assertEqual(
            chars,
            ms.SESSION_BYTES_CAP + ms._INTERNAL_OVERHEAD_CHARS
        )


class AutoselectTests(unittest.TestCase):
    """Phase 29.5.3a: autoselect_os_model — Sonnet default, Haiku opt-in."""

    def test_default_returns_high_sonnet(self) -> None:
        """Default behavior: always return HIGH (Sonnet) regardless of payload size."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_ALLOW_HAIKU", None)
            result = ms.autoselect_os_model(
                1000, threshold=60_000,
                low="haiku-model", high="sonnet-model",
            )
            self.assertEqual(result, "sonnet-model")

    def test_default_large_payload_still_returns_high(self) -> None:
        """Default behavior: even large payloads return HIGH (Sonnet)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_ALLOW_HAIKU", None)
            result = ms.autoselect_os_model(
                1_000_000, threshold=60_000,
                low="haiku-model", high="sonnet-model",
            )
            self.assertEqual(result, "sonnet-model")

    def test_haiku_allowed_below_threshold_returns_low(self) -> None:
        """With CORVIN_OS_MODEL_ALLOW_HAIKU=1: use adaptive logic."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "1"}):
            result = ms.autoselect_os_model(
                1000, threshold=60_000,
                low="haiku-model", high="sonnet-model",
            )
            self.assertEqual(result, "haiku-model")

    def test_haiku_allowed_at_threshold_returns_low(self) -> None:
        """With CORVIN_OS_MODEL_ALLOW_HAIKU=1: at threshold still returns LOW."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "1"}):
            result = ms.autoselect_os_model(
                60_000, threshold=60_000,
                low="haiku-model", high="sonnet-model",
            )
            self.assertEqual(result, "haiku-model")

    def test_haiku_allowed_above_threshold_returns_high(self) -> None:
        """With CORVIN_OS_MODEL_ALLOW_HAIKU=1: above threshold returns HIGH."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "1"}):
            result = ms.autoselect_os_model(
                60_001, threshold=60_000,
                low="haiku-model", high="sonnet-model",
            )
            self.assertEqual(result, "sonnet-model")

    def test_default_threshold_used_when_none_with_haiku_allowed(self) -> None:
        """With CORVIN_OS_MODEL_ALLOW_HAIKU=1: uses default threshold."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "1"}):
            result = ms.autoselect_os_model(
                ms.DEFAULT_THRESHOLD_CHARS - 1,
                low="LO", high="HI",
            )
            self.assertEqual(result, "LO")


class EnvTests(unittest.TestCase):
    """Phase 29.5.3a: env-driven helpers."""

    def test_autoselect_on_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_AUTOSELECT", None)
            self.assertTrue(ms.autoselect_enabled())

    def test_autoselect_off(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_AUTOSELECT": "off"}):
            self.assertFalse(ms.autoselect_enabled())

    def test_threshold_too_low_clamped(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_THRESHOLD_CHARS": "5000"}):
            self.assertEqual(ms.threshold_chars(), ms._MIN_THRESHOLD)

    def test_threshold_too_high_clamped(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_THRESHOLD_CHARS": "999999"}):
            self.assertEqual(ms.threshold_chars(), ms._MAX_THRESHOLD)

    def test_threshold_invalid_falls_back(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_THRESHOLD_CHARS": "abc"}):
            self.assertEqual(ms.threshold_chars(), ms.DEFAULT_THRESHOLD_CHARS)

    def test_override_env_returns_model(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_OVERRIDE": "claude-opus-4-7"}):
            self.assertEqual(ms.os_model_override(), "claude-opus-4-7")

    def test_override_env_empty_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_OVERRIDE", None)
            self.assertIsNone(ms.os_model_override())

    def test_haiku_downgrade_not_allowed_by_default(self) -> None:
        """Default: Haiku downgrade is NOT allowed."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_ALLOW_HAIKU", None)
            self.assertFalse(ms.haiku_downgrade_allowed())

    def test_haiku_downgrade_allowed_with_1(self) -> None:
        """CORVIN_OS_MODEL_ALLOW_HAIKU=1 enables Haiku downgrade."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "1"}):
            self.assertTrue(ms.haiku_downgrade_allowed())

    def test_haiku_downgrade_allowed_with_true(self) -> None:
        """CORVIN_OS_MODEL_ALLOW_HAIKU=true enables Haiku downgrade."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "true"}):
            self.assertTrue(ms.haiku_downgrade_allowed())

    def test_haiku_downgrade_allowed_with_yes(self) -> None:
        """CORVIN_OS_MODEL_ALLOW_HAIKU=yes enables Haiku downgrade."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "yes"}):
            self.assertTrue(ms.haiku_downgrade_allowed())

    def test_haiku_downgrade_allowed_with_on(self) -> None:
        """CORVIN_OS_MODEL_ALLOW_HAIKU=on enables Haiku downgrade."""
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_ALLOW_HAIKU": "on"}):
            self.assertTrue(ms.haiku_downgrade_allowed())


class ContextErrorTests(unittest.TestCase):
    """Phase 29.5.3a: is_context_error."""

    def test_known_pattern_matches_case_insensitive(self) -> None:
        self.assertTrue(ms.is_context_error("Autocompact is Thrashing now"))
        self.assertTrue(ms.is_context_error("prompt is too long for model"))
        self.assertTrue(ms.is_context_error("Error: context_length_exceeded"))
        self.assertTrue(ms.is_context_error("input length exceeds limit"))

    def test_unknown_pattern_returns_false(self) -> None:
        self.assertFalse(ms.is_context_error("network timeout"))
        self.assertFalse(ms.is_context_error("rate limit exceeded"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(ms.is_context_error(""))

    def test_non_string_returns_false(self) -> None:
        self.assertFalse(ms.is_context_error(None))  # type: ignore


class FloorTests(unittest.TestCase):
    """Phase 29.5.3a: apply_floor."""

    def test_floor_sonnet_upgrades_haiku(self) -> None:
        result = ms.apply_floor(ms.DEFAULT_LOW, "sonnet")
        self.assertEqual(result, ms.DEFAULT_HIGH)

    def test_floor_haiku_keeps_sonnet(self) -> None:
        result = ms.apply_floor(ms.DEFAULT_HIGH, "haiku")
        self.assertEqual(result, ms.DEFAULT_HIGH)

    def test_floor_none_returns_chosen(self) -> None:
        self.assertEqual(ms.apply_floor("anything", None), "anything")

    def test_floor_empty_returns_chosen(self) -> None:
        self.assertEqual(ms.apply_floor("anything", ""), "anything")


class AuditValidationTests(unittest.TestCase):
    """Phase 29.5.3d: emit_selected and emit_escalated contract."""

    def _fake_write(self):
        """Patch write_event to capture calls without real chain."""
        return mock.patch.object(ms, "_write_event", return_value=None)

    def test_emit_selected_happy_path(self) -> None:
        with self._fake_write() as w:
            ms.emit_selected(
                persona="coder", channel="discord",
                estimate_chars=1000, chosen=ms.DEFAULT_LOW,
                reason="autoselect_low",
            )
            w.assert_called_once()
            call_args = w.call_args
            self.assertEqual(call_args[0][0], "os_model.selected")
            details = call_args[0][1]
            self.assertEqual(details["chosen"], "haiku")
            self.assertEqual(details["reason"], "autoselect_low")

    def test_emit_selected_rejects_forbidden_prompt_field(self) -> None:
        # patch _validate_details to raise on forbidden field
        with self.assertRaises(ms.OsModelAuditFieldNotAllowed):
            # Call _validate_details directly to verify it rejects
            ms._validate_details(
                {"prompt": "secret"},
                ms._ALLOWED_FIELDS_SELECTED,
                "os_model.selected",
            )

    def test_emit_selected_rejects_unknown_reason(self) -> None:
        with self.assertRaises(ms.OsModelAuditFieldNotAllowed):
            ms.emit_selected(
                persona="p", channel="c", estimate_chars=0,
                chosen=ms.DEFAULT_LOW, reason="unknown_bad_reason",
            )

    def test_emit_escalated_happy_path(self) -> None:
        with self._fake_write() as w:
            ms.emit_escalated(
                persona="forge", channel="discord",
                from_model=ms.DEFAULT_LOW, to_model=ms.DEFAULT_HIGH,
                reason="autocompact-thrash",
            )
            w.assert_called_once()
            details = w.call_args[0][1]
            self.assertEqual(details["from"], "haiku")
            self.assertEqual(details["to"], "sonnet")

    def test_emit_escalated_rejects_unknown_reason(self) -> None:
        with self.assertRaises(ms.OsModelAuditFieldNotAllowed):
            ms.emit_escalated(
                persona="p", channel="c",
                from_model=ms.DEFAULT_LOW, to_model=ms.DEFAULT_HIGH,
                reason="bad_reason",
            )

    def test_unknown_model_curated_to_other(self) -> None:
        with self._fake_write() as w:
            ms.emit_selected(
                persona="p", channel="c", estimate_chars=0,
                chosen="some-future-model-xyz",
                reason="explicit",
            )
            details = w.call_args[0][1]
            self.assertEqual(details["chosen"], "other")

    def test_chain_integrity_via_classify_reason(self) -> None:
        self.assertEqual(
            ms.classify_error_reason("Autocompact is thrashing"),
            "autocompact-thrash",
        )
        self.assertEqual(
            ms.classify_error_reason("HTTP 400 context exceeded"),
            "http-400",
        )
        self.assertEqual(
            ms.classify_error_reason("input length exceeds limit"),
            "context-overflow",
        )

    def test_emit_selected_off_allowlist_field_raises(self) -> None:
        with self.assertRaises(ms.OsModelAuditFieldNotAllowed):
            ms._validate_details(
                {"unknown_field": "value"},
                ms._ALLOWED_FIELDS_SELECTED,
                "os_model.selected",
            )


class EscalateForErrorTests(unittest.TestCase):
    """Phase 29.5.3a + e: escalate_for_error."""

    def test_context_error_escalates(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_RETRY_ON_THRASH": "on"}):
            result = ms.escalate_for_error(
                "Autocompact is thrashing",
                current=ms.DEFAULT_LOW,
            )
            self.assertEqual(result, ms.high_model())

    def test_non_context_error_no_escalation(self) -> None:
        result = ms.escalate_for_error("network timeout", current=ms.DEFAULT_LOW)
        self.assertIsNone(result)

    def test_already_high_no_escalation(self) -> None:
        result = ms.escalate_for_error(
            "Autocompact is thrashing",
            current=ms.DEFAULT_HIGH,
        )
        self.assertIsNone(result)

    def test_retry_disabled_no_escalation(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_OS_MODEL_RETRY_ON_THRASH": "off"}):
            result = ms.escalate_for_error(
                "Autocompact is thrashing",
                current=ms.DEFAULT_LOW,
            )
            self.assertIsNone(result)


class TransientHttpErrorTests(unittest.TestCase):
    """is_transient_http_error + parse_retry_after_seconds (HTTP-reset path).

    These tests guard the four observed 400-failures in production where
    the Anthropic CLI surfaced a bare HTTP status without a body.
    """

    def test_bare_status_codes(self) -> None:
        # 404 is included: `claude --resume <id>` for a server-evicted session
        # returns a naked 404; it must trigger reset + retry, not propagate.
        for code in ("400", "404", "408", "409", "429", "500", "502", "503", "504", "529"):
            with self.subTest(code=code):
                self.assertTrue(ms.is_transient_http_error(code))

    def test_success_and_unknown_codes_not_transient(self) -> None:
        for code in ("200", "201", "301", "418"):
            with self.subTest(code=code):
                self.assertFalse(ms.is_transient_http_error(code))

    def test_404_is_session_corrupting(self) -> None:
        # A 404 on resume means the on-disk session id is stale → wipe + fresh.
        self.assertTrue(ms.is_session_corrupting_http_error("404"))
        # but a pure transient must NOT wipe the local session state.
        self.assertFalse(ms.is_session_corrupting_http_error("429"))

    def test_word_boundary_rejects_substring(self) -> None:
        # "1400" must not match — the regex uses \b boundaries.
        self.assertFalse(ms.is_transient_http_error("1400 something"))
        self.assertFalse(ms.is_transient_http_error("paper-page-4000"))

    def test_symbolic_tokens(self) -> None:
        for token in ("rate_limited", "RATE_LIMITED",
                      "overloaded_error", "internal_server_error",
                      "service_unavailable", "api_error_status",
                      "request_too_large"):
            with self.subTest(token=token):
                self.assertTrue(ms.is_transient_http_error(token))

    def test_connection_level_errors_transient(self) -> None:
        # Incident 2026-07-10: a local network outage (hotspot drop) killed
        # a running turn with zero retries because connection-level failures
        # were not classified as transient. The exact production strings:
        for text in (
            "API Error: Unable to connect to API (ConnectionRefused)",
            "connect ECONNREFUSED 127.0.0.1:8082",
            "getaddrinfo ENOTFOUND api.anthropic.com",
            "Connection error.",
            "Cannot connect to host speech.platform.bing.com:443 "
            "[Name or service not known]",
            "connect ETIMEDOUT 160.79.104.10:443",
            "Network is unreachable",
        ):
            with self.subTest(text=text):
                self.assertTrue(ms.is_transient_http_error(text))

    def test_connection_level_errors_do_not_wipe_session(self) -> None:
        # Connection failures never reached the API — on-disk session state
        # is intact and must be preserved on retry (no wipe).
        for text in (
            "API Error: Unable to connect to API (ConnectionRefused)",
            "getaddrinfo ENOTFOUND api.anthropic.com",
        ):
            with self.subTest(text=text):
                self.assertFalse(ms.is_session_corrupting_http_error(text))

    def test_idle_timeout_not_transient_http(self) -> None:
        # Stream-idle has its own reset trigger in the adapter; it must
        # NOT also be classified as HTTP-transient.
        self.assertFalse(ms.is_transient_http_error(
            "Stream idle timeout — Claude lieferte 300s lang keine Events mehr"
        ))

    def test_sigterm_exit_not_transient(self) -> None:
        self.assertFalse(ms.is_transient_http_error(
            "claude exited without result (exit_code=143)"
        ))

    def test_context_overflow_not_misclassified(self) -> None:
        # "prompt is too long" stays the escalate_for_error trigger; it
        # must not also reset the session via the HTTP path.
        self.assertFalse(ms.is_transient_http_error(
            "prompt is too long"
        ))

    def test_empty_and_none(self) -> None:
        self.assertFalse(ms.is_transient_http_error(""))
        self.assertFalse(ms.is_transient_http_error(None))  # type: ignore[arg-type]

    def test_retry_after_seconds(self) -> None:
        self.assertEqual(
            ms.parse_retry_after_seconds("rate_limit, retry-after: 12"), 12,
        )
        self.assertEqual(
            ms.parse_retry_after_seconds("Retry-After 8"), 8,
        )
        self.assertEqual(
            ms.parse_retry_after_seconds("please retry in 3 seconds"), 5,
            "below min_seconds=5 must clamp up",
        )
        self.assertEqual(
            ms.parse_retry_after_seconds("wait 999 seconds"), 120,
            "above max_seconds=120 must clamp down",
        )
        self.assertIsNone(ms.parse_retry_after_seconds("no hint"))
        self.assertEqual(
            ms.parse_retry_after_seconds("no hint", default=8), 8,
            "default kicks in when no hint",
        )


class NoSdkImportContractTests(unittest.TestCase):
    """Phase 29.5.3a: model_selector.py must not import anthropic / openai."""

    def test_no_anthropic_or_openai_import(self) -> None:
        src = Path(__file__).parent / "model_selector.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [n.name for n in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    if name and (
                        name == "anthropic"
                        or name.startswith("anthropic.")
                        or name == "openai"
                        or name.startswith("openai.")
                        or name.startswith("google.generativeai")
                    ):
                        self.fail(
                            f"model_selector.py must not import LLM SDKs "
                            f"(found: {name!r}). Cost contract violation."
                        )


if __name__ == "__main__":
    unittest.main()
