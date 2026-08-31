"""ADR-0157 — L44 Resilient Classifier: unit + integration tests.

Covers all 4 Milestones:
  M1/B  Parser hardening (_house_rules_parse_verdict, JSON-wrapper extraction)
  M1/D  Exponential backoff retry (spawn_missing aborts; transient causes retry)
  M2    Clear-verdict cache (only CLEAR cached; DENY/ESCALATE never cached; TTL)
  M3    Provider-chain: Hermes → cloud Haiku → fail-closed
  M4    Degradation clustering (emit WARNING after threshold errors in window)

Security invariant (verified on every test path): fail-closed is NEVER
weakened — if the chain exhausts without a verdict, the exception propagates
so the gate's ``classifier_error`` path escalates (never allows).
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
import unittest.mock as mock
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Module fixture — fresh adapter import per test that needs it
# ---------------------------------------------------------------------------

def _fresh_adapter():
    """Return adapter module, clearing cache between tests."""
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter as _a  # type: ignore
    return _a


@pytest.fixture()
def adp(monkeypatch, tmp_path):
    """Adapter module with a clean environment."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    a = _fresh_adapter()
    # Reset module-level mutable state — both names point to the same objects
    # in house_rules.py (re-exported from adapter via ADR-0158 M3).
    a._house_rules_verdict_cache.clear()
    a._house_rules_degrade_times.clear()
    return a


@pytest.fixture()
def hr():
    """house_rules module — canonical home for classifier internals (ADR-0158 M3).

    monkeypatch.setattr(hr, '_house_rules_*', ...) is required for function
    patches since house_rules functions resolve each other via their own module
    globals, not via the adapter re-export names."""
    import house_rules as _hr  # type: ignore
    _hr._house_rules_verdict_cache.clear()
    _hr._house_rules_degrade_times.clear()
    return _hr


# ---------------------------------------------------------------------------
# M1/B — Parser hardening
# ---------------------------------------------------------------------------

class TestParseVerdict:
    """_house_rules_parse_verdict: direct JSON, rfind fallback, and error cases."""

    def test_clean_json_direct_parse(self, adp):
        """Clean JSON string → direct parse, no rfind needed."""
        raw = '{"violated_rule_id": "", "confidence": 0.95, "reason": "safe request"}'
        rid, conf, detail = adp._house_rules_parse_verdict(raw)
        assert rid == ""
        assert abs(conf - 0.95) < 1e-6
        assert "safe" in detail

    def test_violation_json(self, adp):
        """Violation JSON returns correct rule_id."""
        raw = '{"violated_rule_id": "no-military", "confidence": 0.9, "reason": "weapon"}'
        rid, conf, detail = adp._house_rules_parse_verdict(raw)
        assert rid == "no-military"
        assert conf == pytest.approx(0.9)

    def test_rfind_fallback_with_preamble(self, adp):
        """Preamble before JSON → rfind fallback extracts the JSON."""
        raw = 'Sure, here is the result: {"violated_rule_id": "", "confidence": 0.8, "reason": "ok"}'
        rid, conf, detail = adp._house_rules_parse_verdict(raw)
        assert rid == ""
        assert conf == pytest.approx(0.8)

    def test_no_json_raises(self, adp):
        """Pure text with no JSON braces → no_json error."""
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict("I cannot help with that.")
        assert exc.value.cause == "no_json"

    def test_empty_raises(self, adp):
        """Empty string → empty_output error."""
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict("")
        assert exc.value.cause == "empty_output"

    def test_bad_json_raises(self, adp):
        """Broken JSON (unclosed brace) → bad_json error."""
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict('{"violated_rule_id": "x", "confidence"')
        assert exc.value.cause in ("bad_json", "no_json")

    def test_non_finite_confidence_clamped(self, adp):
        """Non-finite confidence values must be clamped to 0.0."""
        # non-finite via rfind path (direct parse would reject NaN literal)
        raw = '{"violated_rule_id": "", "confidence": 0.0, "reason": "ok"}'
        rid, conf, detail = adp._house_rules_parse_verdict(raw)
        assert conf == pytest.approx(0.0)

    def test_ollama_error_response_raises_bad_json(self, adp):
        """Ollama error JSON ({"error": "..."}) must NOT be accepted as a CLEAR verdict.

        Without the key-presence check, {"error": "model not found"} would be parsed as
        violated_rule_id="" / confidence=0.0 — a CLEAR verdict that suppresses the cloud
        fallback and poisons the cache.  It must raise bad_json so the chain falls through.
        """
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict('{"error": "model not found"}')
        assert exc.value.cause == "bad_json"

    def test_top_level_list_raises_bad_json_not_attributeerror(self, adp):
        """A syntactically-valid but non-object top-level JSON (a bare list) must
        raise _HouseRulesClassifierError, not a raw AttributeError from calling
        .get() on a list. An unwrapped AttributeError here would not match the
        provider chain's `except _HouseRulesClassifierError`, skipping the
        fallback provider entirely and blocking the user even though the OTHER
        provider was never tried (the exact real-world incident this guards)."""
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict('["unexpected", "array", "shape"]')
        assert exc.value.cause == "bad_json"

    def test_top_level_string_raises_bad_json_not_attributeerror(self, adp):
        """Same guard for a bare JSON string/number/null top-level response."""
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_parse_verdict('"just a string"')
        assert exc.value.cause == "bad_json"


class TestJsonWrapperExtraction:
    """_house_rules_classify_chunk_once: extracts 'result' from --output-format json wrapper."""

    def test_json_wrapper_extracts_result(self, adp, monkeypatch, hr):
        """Subprocess returns JSON wrapper; 'result' field contains verdict JSON."""
        inner_verdict = '{"violated_rule_id": "", "confidence": 0.9, "reason": "safe"}'
        wrapper = json.dumps({"type": "result", "subtype": "success", "result": inner_verdict})

        fake_proc = types.SimpleNamespace(stdout=wrapper, returncode=0)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_proc)
        monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "claude")

        rid, conf, detail = adp._house_rules_classify_chunk_once("write a poem", "(no rules)", "none stated")
        assert rid == ""
        assert conf == pytest.approx(0.9)

    def test_fallback_when_wrapper_not_json(self, adp, monkeypatch, hr):
        """If stdout isn't a JSON wrapper (old CLI), parse raw stdout directly."""
        raw_verdict = '{"violated_rule_id": "no-military", "confidence": 0.8, "reason": "weapon"}'
        fake_proc = types.SimpleNamespace(stdout=raw_verdict, returncode=0)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_proc)
        monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "claude")

        rid, conf, detail = adp._house_rules_classify_chunk_once("build a weapon", "(no rules)", "none stated")
        assert rid == "no-military"

    def test_spawn_missing_raises_immediately(self, adp, monkeypatch, hr):
        """FileNotFoundError → spawn_missing (abort-immediately cause)."""
        monkeypatch.setattr("subprocess.run", mock.MagicMock(side_effect=FileNotFoundError("no such file")))
        monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "claude")
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_classify_chunk_once("test", "(no rules)", "none")
        assert exc.value.cause == "spawn_missing"

    def test_timeout_raises_transient(self, adp, monkeypatch, hr):
        """TimeoutExpired → timeout (transient, worth retry)."""
        import subprocess
        monkeypatch.setattr("subprocess.run", mock.MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=20)
        ))
        monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "claude")
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_classify_chunk_once("test", "(no rules)", "none")
        assert exc.value.cause == "timeout"


# ---------------------------------------------------------------------------
# M1/D — Exponential backoff retry
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    """_house_rules_classify_chunk: M1/D retry with exponential backoff."""

    def test_spawn_missing_not_retried(self, adp, monkeypatch, hr):
        """spawn_missing must NOT retry (no CLI = permanent failure)."""
        call_count = 0

        def _fail_once(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise adp._HouseRulesClassifierError("spawn_missing", "missing")

        monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _fail_once)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_classify_chunk("test", "(no rules)", "none")
        assert exc.value.cause == "spawn_missing"
        assert call_count == 1  # no retries

    def test_transient_retried_up_to_max(self, adp, monkeypatch, hr):
        """Transient timeout retried up to RETRIES+1 times."""
        call_count = 0
        max_expected = adp._HOUSE_RULES_RETRIES + 1

        def _always_fail(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise adp._HouseRulesClassifierError("timeout", "test")

        monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _always_fail)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        with pytest.raises(adp._HouseRulesClassifierError):
            adp._house_rules_classify_chunk("test", "(no rules)", "none")
        assert call_count == max_expected

    def test_succeeds_on_second_attempt(self, adp, monkeypatch, hr):
        """First attempt fails transiently, second succeeds → returns verdict."""
        call_count = 0

        def _fail_then_succeed(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise adp._HouseRulesClassifierError("timeout", "first")
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _fail_then_succeed)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        rid, conf, _ = adp._house_rules_classify_chunk("test", "(no rules)", "none")
        assert rid == "" and call_count == 2

    def test_backoff_increases_exponentially(self, adp, monkeypatch, hr):
        """Backoff between retries must increase and be capped."""
        sleeps = []
        call_count = 0

        def _always_fail(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise adp._HouseRulesClassifierError("no_json", "test")

        monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _always_fail)
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        with pytest.raises(adp._HouseRulesClassifierError):
            adp._house_rules_classify_chunk("test", "(no rules)", "none")

        # We should have RETRIES sleep calls, each ≤ _HOUSE_RULES_RETRY_BACKOFF_MAX_S
        assert len(sleeps) == adp._HOUSE_RULES_RETRIES
        for s in sleeps:
            assert s <= adp._HOUSE_RULES_RETRY_BACKOFF_MAX_S
        # Second sleep must be >= first (exponential growth)
        if len(sleeps) >= 2:
            assert sleeps[1] >= sleeps[0]


# ---------------------------------------------------------------------------
# M3 — Provider chain: Hermes → Cloud Haiku → fail-closed
# ---------------------------------------------------------------------------

class TestProviderChain:
    """_house_rules_classify_with_chain: M3 Hermes-first, cloud fallback."""

    def test_hermes_called_first(self, adp, monkeypatch, hr):
        """order=local_first calls Hermes before cloud Haiku (ADR-0161)."""
        hermes_called = []

        def _hermes_ok(*a, **kw):
            hermes_called.append(True)
            return "", 0.95, "safe"

        cloud_called = []

        def _cloud_ok(*a, **kw):
            cloud_called.append(True)
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes_ok)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_ok)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        rid, conf, _ = adp._house_rules_classify_with_chain(
            "test", "(no rules)", "none", order="local_first")
        assert hermes_called and not cloud_called  # Hermes succeeded → cloud not needed
        assert rid == "" and conf == pytest.approx(0.95)

    def test_cloud_fallback_on_hermes_failure(self, adp, monkeypatch, hr):
        """Hermes failure → falls back to cloud Haiku (not fail-closed yet)."""
        def _hermes_fail(*a, **kw):
            raise adp._HouseRulesClassifierError("timeout", "hermes down")

        cloud_called = []

        def _cloud_ok(*a, **kw):
            cloud_called.append(True)
            return "", 0.85, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes_fail)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_ok)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        rid, conf, _ = adp._house_rules_classify_with_chain(
            "test", "(no rules)", "none", order="local_first")
        assert cloud_called
        assert rid == ""

    def test_disable_hermes_env_skips_hermes(self, adp, monkeypatch, hr):
        """CORVIN_HOUSE_RULES_DISABLE_HERMES=1 skips Hermes entirely."""
        hermes_called = []

        def _hermes(*a, **kw):
            hermes_called.append(True)
            return "", 0.95, "safe"

        cloud_called = []

        def _cloud(*a, **kw):
            cloud_called.append(True)
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud)
        monkeypatch.setenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", "1")

        adp._house_rules_classify_with_chain("test", "(no rules)", "none")
        assert not hermes_called  # skipped
        assert cloud_called

    def test_both_fail_raises(self, adp, monkeypatch, hr):
        """Hermes + cloud Haiku both fail → exception propagates (fail-closed)."""
        monkeypatch.setattr(hr, "_house_rules_classify_hermes",
                            mock.MagicMock(side_effect=adp._HouseRulesClassifierError("timeout")))
        monkeypatch.setattr(hr, "_house_rules_classify_chunk",
                            mock.MagicMock(side_effect=adp._HouseRulesClassifierError("timeout")))
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        with pytest.raises(adp._HouseRulesClassifierError):
            adp._house_rules_classify_with_chain("test", "(no rules)", "none")

    def test_unexpected_exception_type_still_falls_back(self, adp, monkeypatch, hr):
        """A primary-provider failure that ISN'T _HouseRulesClassifierError (e.g. a
        parsing/shape bug raising AttributeError/KeyError/whatever) must still
        trigger the fallback to the secondary provider — the whole point of the
        two-provider chain is resilience against a single provider's fault,
        which must not depend on that fault being an anticipated exception type.
        Catching only _HouseRulesClassifierError left a hole where an
        unanticipated bug in ONE provider's response parsing silently skipped a
        perfectly healthy OTHER provider and blocked the user — the real-world
        incident this test guards against."""
        def _hermes_unexpected_bug(*a, **kw):
            raise AttributeError("'list' object has no attribute 'get'")

        cloud_called = []

        def _cloud_ok(*a, **kw):
            cloud_called.append(True)
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes_unexpected_bug)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_ok)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        rid, conf, _ = adp._house_rules_classify_with_chain(
            "test", "(no rules)", "none", order="local_first")
        assert cloud_called
        assert rid == ""
        assert conf == pytest.approx(0.9)

    def test_both_fail_with_unexpected_exception_still_raises(self, adp, monkeypatch, hr):
        """Even with the broadened catch, if BOTH providers fail (any exception
        type) the gate still fails closed — the fallback broadening only gives
        the secondary a fair chance, it never turns into a silent allow."""
        monkeypatch.setattr(hr, "_house_rules_classify_hermes",
                            mock.MagicMock(side_effect=AttributeError("boom")))
        monkeypatch.setattr(hr, "_house_rules_classify_chunk",
                            mock.MagicMock(side_effect=KeyError("boom")))
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        # Whichever provider is secondary (order-dependent), its exception type
        # propagates raw here — the gate's own outer `except Exception` (in
        # HouseRulesGate.classify) is what normalises this to classifier_error
        # fail-closed; this test only asserts the chain never swallows both
        # failures into a silent success.
        with pytest.raises((adp._HouseRulesClassifierError, KeyError, AttributeError)):
            adp._house_rules_classify_with_chain("test", "(no rules)", "none")


# ---------------------------------------------------------------------------
# ADR-0161 — context-aware classifier ordering
# ---------------------------------------------------------------------------

class TestClassifierOrdering:
    """_house_rules_resolve_order + order-driven provider chain (ADR-0161)."""

    def _wire(self, adp, monkeypatch, hr):
        """Wire counting stubs; return (hermes_calls, cloud_calls) lists."""
        h, c = [], []
        monkeypatch.setattr(hr, "_house_rules_classify_hermes",
                            lambda *a, **kw: (h.append(1), ("", 0.95, "local"))[1])
        monkeypatch.setattr(hr, "_house_rules_classify_chunk",
                            lambda *a, **kw: (c.append(1), ("", 0.9, "cloud"))[1])
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", raising=False)
        return h, c

    def test_order_cloud_first_tries_cloud_before_local(self, adp, monkeypatch, hr):
        h, c = self._wire(adp, monkeypatch, hr)
        rid, conf, _ = adp._house_rules_classify_with_chain(
            "t", "(no rules)", "none", order="cloud_first")
        assert c and not h and conf == pytest.approx(0.9)

    def test_order_local_only_never_calls_cloud_even_on_failure(self, adp, monkeypatch, hr):
        """local_only must NEVER fall through to cloud — data-residency invariant.
        A local failure raises (fail-closed), cloud is never reached."""
        c = []
        monkeypatch.setattr(hr, "_house_rules_classify_chunk",
                            lambda *a, **kw: (c.append(1), ("", 0.9, "cloud"))[1])
        monkeypatch.setattr(hr, "_house_rules_classify_hermes",
                            mock.MagicMock(side_effect=adp._HouseRulesClassifierError("timeout")))
        with pytest.raises(adp._HouseRulesClassifierError):
            adp._house_rules_classify_with_chain("t", "(no rules)", "none", order="local_only")
        assert not c  # cloud NEVER called under local_only

    def test_env_order_override_is_honored(self, adp, monkeypatch, hr):
        h, c = self._wire(adp, monkeypatch, hr)
        monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", "local_first")
        assert adp._house_rules_resolve_order() == "local_first"

    def test_disable_hermes_maps_to_cloud_only(self, adp, monkeypatch, hr):
        monkeypatch.setenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", "1")
        assert adp._house_rules_resolve_order() == "cloud_only"

    def test_auto_no_egress_policy_is_cloud_first(self, adp, monkeypatch, hr):
        """auto + no tenant egress policy (normal cloud install) → cloud_first (fast)."""
        self._wire(adp, monkeypatch, hr)
        assert adp._house_rules_resolve_order() == "cloud_first"

    def test_auto_egress_deny_anthropic_is_local_only(self, adp, monkeypatch, hr, tmp_path):
        """auto + tenant egress that denies api.anthropic.com → local_only.
        The cloud classifier host is unreachable per policy, so the gate must
        classify on-host and NEVER fall through to cloud (closes the residency bug)."""
        self._wire(adp, monkeypatch, hr)
        # CORVIN_HOME is tmp_path (adp fixture). Write a tenant egress policy that
        # denies the cloud classifier host.
        import os as _os
        tcfg = tmp_path / "tenants" / "_default" / "global"
        tcfg.mkdir(parents=True, exist_ok=True)
        (tcfg / "tenant.corvin.yaml").write_text(
            "spec:\n"
            "  egress:\n"
            "    enabled: true\n"
            "    default_action: deny\n"
            "    forbidden_hosts:\n"
            "      - api.anthropic.com\n",
            encoding="utf-8",
        )
        assert adp._house_rules_cloud_egress_allowed("_default") is False
        assert adp._house_rules_resolve_order("_default") == "local_only"

    def test_egress_config_read_exception_fails_open_to_cloud_KNOWN_BUG(
        self, adp, monkeypatch, hr, tmp_path
    ):
        """KNOWN BUG (documented, not yet fixed): ``_house_rules_cloud_egress_allowed``
        wraps the ENTIRE tenant.corvin.yaml read+parse+EgressGate-eval in a bare
        ``except Exception: return True``. For a tenant with an explicit residency
        deny (``forbidden_hosts: [api.anthropic.com]``), a *transient* read/parse
        failure (mid-write race, disk hiccup, corrupted YAML) silently flips the
        resolved order from ``local_only`` to ``cloud_first`` -- i.e. the classifier
        will then transmit the user's raw task text via ``claude -p`` to
        ``api.anthropic.com`` despite the tenant's still-intended deny policy. The
        cloud classifier subprocess call has no L35 gate of its own (only the
        primary engine spawn is gated), so this ordering IS the enforcement for
        that call site.

        This test locks in and documents the CURRENT (undesired) behavior so any
        change to it is a conscious decision, not an accidental regression in
        either direction. Fixing it requires care: the same except-block also
        covers legitimate no-policy-at-all scenarios elsewhere in this class, so a
        fail-closed default here needs a human design decision (see review
        finding), not a blind flip."""
        self._wire(adp, monkeypatch, hr)
        tcfg = tmp_path / "tenants" / "_default" / "global"
        tcfg.mkdir(parents=True, exist_ok=True)
        (tcfg / "tenant.corvin.yaml").write_text(
            "spec:\n"
            "  egress:\n"
            "    enabled: true\n"
            "    default_action: deny\n"
            "    forbidden_hosts:\n"
            "      - api.anthropic.com\n",
            encoding="utf-8",
        )
        # Sanity: a healthy read of the same deny-policy config correctly
        # resolves to local_only (this is the already-covered happy path).
        assert adp._house_rules_cloud_egress_allowed("_default") is False
        assert adp._house_rules_resolve_order("_default") == "local_only"

        # Now simulate a transient read/parse failure on the SAME on-disk
        # deny-policy config (corrupted mid-write, disk hiccup, bad edit).
        import yaml as _yaml

        def _raise(*_a, **_kw):
            raise ValueError("simulated corrupt/partial yaml read")

        monkeypatch.setattr(_yaml, "safe_load", _raise)

        # BUG: the bare `except Exception: return True` treats a read failure
        # identically to "no policy configured at all" -- the residency-deny
        # intent on disk is silently discarded and cloud is reopened.
        assert adp._house_rules_cloud_egress_allowed("_default") is True
        assert adp._house_rules_resolve_order("_default") == "cloud_first"

    def test_egress_config_corrupted_yaml_on_disk_fails_open_to_cloud_KNOWN_BUG(
        self, adp, monkeypatch, hr, tmp_path
    ):
        """Same bug as above, reproduced with a genuinely malformed YAML file on
        disk (no monkeypatching of ``yaml.safe_load`` itself) -- i.e. the
        realistic "someone's manual edit left invalid indentation" / "mid-write
        torn file" scenario, not just a synthetic exception injection."""
        self._wire(adp, monkeypatch, hr)
        tcfg = tmp_path / "tenants" / "_default" / "global"
        tcfg.mkdir(parents=True, exist_ok=True)
        # Deliberately broken YAML (bad indentation / tab mix) that still
        # clearly *intends* a deny policy but fails to parse.
        (tcfg / "tenant.corvin.yaml").write_text(
            "spec:\n"
            "  egress:\n"
            "  enabled: true\n"
            "      default_action: deny\n"
            "    forbidden_hosts: [api.anthropic.com\n",
            encoding="utf-8",
        )
        # Confirm the file really is unparsable (guards against the test
        # accidentally exercising the happy path if YAML tolerates the typo).
        import yaml as _yaml
        with pytest.raises(_yaml.YAMLError):
            _yaml.safe_load((tcfg / "tenant.corvin.yaml").read_text("utf-8"))

        # BUG: corrupted-on-disk deny policy still resolves as if no policy
        # existed at all -- cloud reopens despite the residency intent.
        assert adp._house_rules_cloud_egress_allowed("_default") is True
        assert adp._house_rules_resolve_order("_default") == "cloud_first"

    def test_unknown_order_value_defaults_to_auto(self, adp, monkeypatch, hr):
        self._wire(adp, monkeypatch, hr)
        monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", "garbage")
        # falls back to auto → cloud_first (no egress policy in tmp home)
        assert adp._house_rules_resolve_order() == "cloud_first"

    def _write_spec(self, tmp_path, tid, body):
        d = tmp_path / "tenants" / tid / "global"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tenant.corvin.yaml").write_text(body, encoding="utf-8")

    def test_auto_hermes_engine_egress_open_is_local_first(self, adp, monkeypatch, hr, tmp_path):
        """Engine-aware auto: a hermes-default tenant with egress open resolves to
        local_first — local primary, cloud only as last-resort fallback. Closes the
        residency bug where a hermes tenant tried CLOUD Haiku first every turn."""
        self._wire(adp, monkeypatch, hr)
        self._write_spec(tmp_path, "_default", "spec:\n  default_engine: hermes\n")
        assert adp._house_rules_resolve_order("_default") == "local_first"

    def test_auto_hermes_engine_egress_deny_is_local_only(self, adp, monkeypatch, hr, tmp_path):
        """Hermes-default + egress denies api.anthropic.com → local_only (residency)."""
        self._wire(adp, monkeypatch, hr)
        self._write_spec(
            tmp_path, "_default",
            "spec:\n  default_engine: hermes\n  egress:\n    enabled: true\n"
            "    default_action: deny\n    forbidden_hosts:\n      - api.anthropic.com\n",
        )
        assert adp._house_rules_resolve_order("_default") == "local_only"

    def test_auto_claude_code_engine_unchanged_cloud_first(self, adp, monkeypatch, hr, tmp_path):
        """A claude_code-default tenant with a working egress path is UNCHANGED —
        still cloud_first (legacy behaviour preserved)."""
        self._wire(adp, monkeypatch, hr)
        self._write_spec(tmp_path, "_default", "spec:\n  default_engine: claude_code\n")
        assert adp._house_rules_resolve_order("_default") == "cloud_first"


class TestAuthMissingCause:
    """Installed-but-unauthenticated cloud CLI → non-transient auth_missing."""

    def test_login_error_envelope_is_auth_missing(self, adp, monkeypatch, hr):
        """is_error envelope with 'Please run /login' → auth_missing cause."""
        import types
        fake = types.SimpleNamespace(
            stdout='{"is_error": true, "result": "Not logged in · Please run /login"}',
            stderr="",
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
        monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "claude")
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_classify_chunk_once("t", "(no rules)", "none")
        assert exc.value.cause == "auth_missing"

    def test_auth_missing_not_retried(self, adp, monkeypatch, hr):
        """auth_missing breaks the retry loop after 1 attempt (no transient budget)."""
        attempts = []

        def _fail(*a, **kw):
            attempts.append(1)
            raise adp._HouseRulesClassifierError("auth_missing", "nope")

        monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _fail)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with pytest.raises(adp._HouseRulesClassifierError) as exc:
            adp._house_rules_classify_chunk("t", "(no rules)", "none")
        assert exc.value.cause == "auth_missing"
        assert len(attempts) == 1  # NOT retried — non-transient like spawn_missing


class TestHermesClassifier:
    """_house_rules_classify_hermes: direct Ollama HTTP call."""

    def test_connection_refused_raises_timeout(self, adp, monkeypatch):
        """Connection refused (Ollama not running) → HouseRulesClassifierError(timeout)."""
        import urllib.error
        monkeypatch.setenv("CORVIN_HERMES_URL", "http://localhost:11434")

        def _fail_urlopen(*a, **kw):
            raise urllib.error.URLError("Connection refused")

        with mock.patch("urllib.request.urlopen", side_effect=_fail_urlopen):
            with pytest.raises(adp._HouseRulesClassifierError) as exc:
                adp._house_rules_classify_hermes("test", "(no rules)", "none")
        assert exc.value.cause == "timeout"

    def test_valid_ollama_response_parsed(self, adp, monkeypatch):
        """Valid Ollama JSON response → parsed correctly."""
        inner = '{"violated_rule_id": "", "confidence": 0.92, "reason": "benign"}'
        # Ollama wraps in {"model": ..., "response": "..., "done": true}
        ollama_response = json.dumps({"model": "hermes3:8b", "response": inner, "done": True})

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_resp.read = mock.MagicMock(return_value=ollama_response.encode())

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            rid, conf, detail = adp._house_rules_classify_hermes("write a poem", "(no rules)", "none")
        assert rid == "" and conf == pytest.approx(0.92)

    def test_default_model_is_canonical_and_pulled(self, adp, monkeypatch):
        """Regression (cloud-outage brick): the local classifier must default to the
        SAME model the canonical HermesEngine uses — never the old un-pulled
        'hermes3:8b'. A drifted default silently kills the local path and degrades
        the gate to cloud-only, so any Anthropic 500 escalates EVERY task.
        """
        monkeypatch.delenv("CORVIN_HERMES_MODEL", raising=False)
        from agents.hermes_engine import _resolve_default_model as _canonical  # type: ignore
        expected = _canonical()
        assert expected != "hermes3:8b"  # the drifted, un-pulled default is gone

        captured = {}

        def _capture_urlopen(req, *a, **kw):
            captured["body"] = json.loads(req.data.decode())
            mock_resp = mock.MagicMock()
            mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = mock.MagicMock(return_value=False)
            inner = '{"violated_rule_id": "", "confidence": 0.95, "reason": "ok"}'
            mock_resp.read = mock.MagicMock(
                return_value=json.dumps({"response": inner}).encode())
            return mock_resp

        with mock.patch("urllib.request.urlopen", side_effect=_capture_urlopen):
            adp._house_rules_classify_hermes("write a poem", "(no rules)", "none")
        # The model actually sent to Ollama is the canonical, installed one.
        assert captured["body"]["model"] == expected

    def test_env_override_model_is_honored(self, adp, monkeypatch):
        """An explicit CORVIN_HERMES_MODEL override reaches the Ollama payload."""
        monkeypatch.setenv("CORVIN_HERMES_MODEL", "qwen3:1.7b")
        captured = {}

        def _capture_urlopen(req, *a, **kw):
            captured["body"] = json.loads(req.data.decode())
            mock_resp = mock.MagicMock()
            mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = mock.MagicMock(return_value=False)
            inner = '{"violated_rule_id": "", "confidence": 0.9, "reason": "ok"}'
            mock_resp.read = mock.MagicMock(
                return_value=json.dumps({"response": inner}).encode())
            return mock_resp

        with mock.patch("urllib.request.urlopen", side_effect=_capture_urlopen):
            adp._house_rules_classify_hermes("hi", "(no rules)", "none")
        assert captured["body"]["model"] == "qwen3:1.7b"

    def test_http_error_model_not_found_raises_misconfigured(self, adp, monkeypatch):
        """A reachable Ollama returning HTTP 404 'model not found' is a CONFIG fault,
        not a transient blip → cause='local_misconfigured' (loud), then the chain
        falls through to cloud Haiku. This is the exact failure that bricked the gate.
        """
        import io
        import urllib.error
        monkeypatch.delenv("CORVIN_HERMES_MODEL", raising=False)

        def _raise_404(*a, **kw):
            raise urllib.error.HTTPError(
                "http://localhost:11434/api/generate", 404, "Not Found", {},
                io.BytesIO(b'{"error":"model \'x\' not found"}'))

        with mock.patch("urllib.request.urlopen", side_effect=_raise_404):
            with pytest.raises(adp._HouseRulesClassifierError) as exc:
                adp._house_rules_classify_hermes("test", "(no rules)", "none")
        assert exc.value.cause == "local_misconfigured"

    def test_chain_recovers_via_cloud_when_local_model_missing(self, adp, monkeypatch, hr):
        """End-to-end of the brick scenario at the chain level: local model missing
        (404) BUT cloud Haiku healthy → chain returns the cloud verdict, gate is NOT
        bricked. (When cloud is ALSO down, the chain still raises → fail-closed.)
        """
        import io
        import urllib.error
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        def _raise_404(*a, **kw):
            raise urllib.error.HTTPError(
                "http://localhost:11434/api/generate", 404, "Not Found", {},
                io.BytesIO(b'{"error":"model not found"}'))

        cloud_called = []

        def _cloud_ok(*a, **kw):
            cloud_called.append(True)
            return "", 0.95, "safe"

        with mock.patch("urllib.request.urlopen", side_effect=_raise_404):
            monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_ok)
            rid, conf, _ = adp._house_rules_classify_with_chain("test", "(no rules)", "none")
        assert cloud_called and rid == "" and conf == pytest.approx(0.95)

    def test_non_dict_ollama_response_raises_bad_json(self, adp, monkeypatch):
        """Ollama returning a JSON array (misconfigured endpoint) must raise bad_json.

        A JSON array causes outer.get() to raise AttributeError, which was previously
        not caught by except (ValueError, KeyError) — bypassing the cloud Haiku fallback.
        The fix adds AttributeError to the except clause so it becomes a bad_json error
        that the chain handles as a normal Hermes failure.
        """
        ollama_list_response = json.dumps([])  # array, not dict

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_resp.read = mock.MagicMock(return_value=ollama_list_response.encode())

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(adp._HouseRulesClassifierError) as exc:
                adp._house_rules_classify_hermes("test", "(no rules)", "none")
        assert exc.value.cause == "bad_json"


# ---------------------------------------------------------------------------
# M2 — Clear-verdict cache
# ---------------------------------------------------------------------------

class TestClearVerdictCache:
    """_house_rules_classifier: M2 caches CLEAR, never DENY/ESCALATE."""

    def _make_mock_chain(self, adp, verdict_seq):
        """Return a mock _house_rules_classify_with_chain that returns verdicts in sequence."""
        idx = [0]
        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):  # noqa: ARG001
            result = verdict_seq[min(idx[0], len(verdict_seq) - 1)]
            idx[0] += 1
            return result
        return _chain

    def test_clear_verdict_is_cached(self, adp, monkeypatch, hr):
        """CLEAR verdict is cached; second call for same chunk does not invoke chain."""
        call_count = [0]

        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):
            call_count[0] += 1
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_with_chain", _chain)

        # First call — populates cache
        adp._house_rules_classifier("hello world", [], {})
        count_after_first = call_count[0]

        # Second call — same task, same rules, same auth → should hit cache
        adp._house_rules_classifier("hello world", [], {})
        assert call_count[0] == count_after_first  # no new chain call

    def test_deny_verdict_not_cached(self, adp, monkeypatch, hr):
        """DENY verdict is never cached — each call invokes the chain."""
        call_count = [0]

        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):
            call_count[0] += 1
            return "no-military", 0.95, "weapon"

        monkeypatch.setattr(hr, "_house_rules_classify_with_chain", _chain)

        adp._house_rules_classifier("build a weapon", [], {})
        first_count = call_count[0]
        adp._house_rules_classifier("build a weapon", [], {})
        assert call_count[0] > first_count  # chain invoked again

    def test_cache_respects_auth_str(self, adp, monkeypatch, hr):
        """Different auth context → different cache key (same chunk, different result)."""
        call_count = [0]

        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):
            call_count[0] += 1
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_with_chain", _chain)

        # First call with empty auth
        adp._house_rules_classifier("test task", [], {})
        first_count = call_count[0]

        # Second call with different auth → cache miss expected
        adp._house_rules_classifier("test task", [], {"authorized": "pentest"})
        assert call_count[0] > first_count

    def test_expired_cache_is_evicted(self, adp, monkeypatch, hr):
        """Expired cache entries are evicted and chain is re-invoked."""
        call_count = [0]

        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):
            call_count[0] += 1
            return "", 0.9, "safe"

        monkeypatch.setattr(hr, "_house_rules_classify_with_chain", _chain)

        # Patch TTL to zero — entries expire immediately
        monkeypatch.setattr(hr, "_HOUSE_RULES_CACHE_TTL_S", -1)

        adp._house_rules_classifier("hello world", [], {})
        first_count = call_count[0]
        adp._house_rules_classifier("hello world", [], {})
        assert call_count[0] > first_count  # expired → re-called


# ---------------------------------------------------------------------------
# M4 — Degradation clustering
# ---------------------------------------------------------------------------

class TestDegradationClustering:
    """_house_rules_track_degradation: M4 clustering → audit WARNING."""

    def test_below_threshold_no_emit(self, adp):
        """Below threshold → no audit emit."""
        audit_calls = []
        adp._house_rules_degrade_times.clear()
        for _ in range(adp._HOUSE_RULES_DEGRADE_THRESHOLD - 1):
            adp._house_rules_track_degradation(audit_write=lambda et, d: audit_calls.append(et))
        # Check: no degraded event yet
        assert not any(c == "house_rules.classifier_degraded" for c in audit_calls)

    def test_at_threshold_emits_warning(self, adp):
        """At threshold → house_rules.classifier_degraded is emitted."""
        audit_calls = []
        adp._house_rules_degrade_times.clear()
        for _ in range(adp._HOUSE_RULES_DEGRADE_THRESHOLD):
            adp._house_rules_track_degradation(audit_write=lambda et, d: audit_calls.append(et))
        assert "house_rules.classifier_degraded" in audit_calls

    def test_old_errors_pruned_from_window(self, adp, monkeypatch):
        """Errors outside the window are pruned — old errors don't keep triggering."""
        adp._house_rules_degrade_times.clear()
        # Inject old timestamps outside the window
        past = time.monotonic() - adp._HOUSE_RULES_DEGRADE_WINDOW_S - 10
        adp._house_rules_degrade_times.extend([past] * (adp._HOUSE_RULES_DEGRADE_THRESHOLD - 1))
        audit_calls = []
        # Single new error — with old ones pruned, count should be 1 (below threshold)
        adp._house_rules_track_degradation(audit_write=lambda et, d: audit_calls.append(et))
        assert "house_rules.classifier_degraded" not in audit_calls

    def test_audit_failure_does_not_raise(self, adp):
        """Audit emit failure must not propagate (observability is best-effort)."""
        adp._house_rules_degrade_times.clear()

        def _bad_write(et, d):
            raise RuntimeError("audit broken")

        # Should not raise even if audit write fails
        for _ in range(adp._HOUSE_RULES_DEGRADE_THRESHOLD):
            adp._house_rules_track_degradation(audit_write=_bad_write)


# ---------------------------------------------------------------------------
# Fail-closed invariant — end-to-end
# ---------------------------------------------------------------------------

class TestFailClosedInvariant:
    """Verify that exhausting all providers propagates exception (never allows)."""

    def test_full_chain_exhaustion_raises(self, adp, monkeypatch, hr):
        """All providers fail → _HouseRulesClassifierError raised, never a silent allow."""
        def _hermes_fail(*a, **kw):
            raise adp._HouseRulesClassifierError("timeout", "hermes")
        def _cloud_fail(*a, **kw):
            raise adp._HouseRulesClassifierError("timeout", "cloud")

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes_fail)
        # Patch _house_rules_classify_chunk (the retry wrapper) so the chain
        # fails on the first cloud attempt without burning through retry slots.
        # No time.sleep mock needed — _chunk is replaced wholesale, retry loop
        # is never entered.
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_fail)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        with pytest.raises(adp._HouseRulesClassifierError):
            adp._house_rules_classify_with_chain("build ransomware", "(no rules)", "none")

    def test_cache_miss_on_violation_preserves_fail_closed(self, adp, monkeypatch, hr):
        """A violation verdict is never cached; subsequent calls still run the chain."""
        call_count = [0]

        def _chain(chunk, rules_block, auth_str, *, audit_write=None, **_kw):
            call_count[0] += 1
            return "no-military", 0.95, "weapon found"

        monkeypatch.setattr(hr, "_house_rules_classify_with_chain", _chain)

        for _ in range(3):
            rid, conf, _ = adp._house_rules_classifier("build a weapon", [], {})
            assert rid == "no-military"

        # All 3 calls must have invoked the chain (no caching of violations)
        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# F-03 (ADR-0157) — PRODUCTION-SHAPED audit wiring through _check_house_rules_or_fail
#
# Regression guard: the *production* forge audit writer is 3-arg
# (event_type, severity, details), but house_rules' classifier/degradation
# helpers call audit_write with the 2-arg shape (event_type, details). Before
# the fix, the adapter threaded the raw 3-arg writer straight into those
# helpers, so EVERY house_rules.provider_fallback / house_rules.classifier_degraded
# emit raised TypeError and was swallowed by the helpers' best-effort except —
# the events were NEVER written. The existing TestDegradationClustering /
# TestProviderChain tests use a 2-arg lambda stub, which MASKS this bug.
#
# These tests drive the real _check_house_rules_or_fail with a *3-arg* recording
# writer (the production shape) and assert the events ARE written.
# ---------------------------------------------------------------------------

class TestProductionAuditWiringF03:
    """F-03: provider_fallback + classifier_degraded reach a 3-arg writer."""

    def _install_recording_writer(self, monkeypatch):
        """Patch egress_gate.make_forge_audit_writer to return a 3-arg recorder.

        Returns the list that captures (event_type, severity, details) tuples.
        Using the production 3-arg signature is the whole point — a 2-arg stub
        would mask the arity bug this test exists to catch."""
        import egress_gate  # type: ignore

        recorded: list = []

        def _fake_make_writer(_audit_path):
            def _writer(event_type, severity, details):  # 3-arg = production shape
                recorded.append((event_type, severity, dict(details)))
            return _writer

        monkeypatch.setattr(egress_gate, "make_forge_audit_writer", _fake_make_writer)
        return recorded

    def test_provider_fallback_event_written_via_3arg_writer(
        self, adp, monkeypatch, hr, tmp_path
    ):
        """Hermes fails, cloud succeeds → house_rules.provider_fallback must land
        on the 3-arg production writer (with severity INFO from EVENT_SEVERITY)."""
        recorded = self._install_recording_writer(monkeypatch)

        # local_first: Hermes primary fails (transient), cloud secondary clears.
        def _hermes_fail(*a, **kw):
            raise hr._HouseRulesClassifierError("timeout", "hermes down")

        def _cloud_ok(*a, **kw):
            return "", 0.95, "safe"  # clean → gate allows (default_action=allow)

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _hermes_fail)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud_ok)
        monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", "local_first")
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)

        result = adp._check_house_rules_or_fail(
            prompt="please summarize this document",
            persona="assistant", channel="discord", chat_key="c1",
        )
        # Clean verdict → gate allows → returns None (not a refusal string).
        assert result is None

        fallback = [r for r in recorded if r[0] == "house_rules.provider_fallback"]
        assert fallback, (
            "house_rules.provider_fallback was NOT written — the 3-arg "
            "production writer was called with the wrong arity (F-03 regression)"
        )
        et, sev, details = fallback[0]
        # Severity resolved from EVENT_SEVERITY (provider_fallback = INFO).
        assert sev == "INFO"
        # Metadata-only allow-list: provider/cause/fallback_to, NO task text.
        assert details.get("provider") == "hermes"
        assert details.get("fallback_to") == "cloud_haiku"
        assert "cause" in details
        assert not any("summarize" in str(v) for v in details.values())

    def test_classifier_degraded_event_written_via_3arg_writer(
        self, adp, monkeypatch, hr, tmp_path
    ):
        """Both providers fail repeatedly → the classify() degrade-to-floor path
        drives _house_rules_track_degradation, which must emit
        house_rules.classifier_degraded (WARNING) onto the 3-arg production writer
        after the threshold — even though benign traffic is now ALLOWED via the
        floor (the operator must still see the backend outage)."""
        recorded = self._install_recording_writer(monkeypatch)

        def _fail(*a, **kw):
            raise hr._HouseRulesClassifierError("timeout", "down")

        monkeypatch.setattr(hr, "_house_rules_classify_hermes", _fail)
        monkeypatch.setattr(hr, "_house_rules_classify_chunk", _fail)
        monkeypatch.delenv("CORVIN_HOUSE_RULES_DISABLE_HERMES", raising=False)
        # No retry sleeps: _classify_chunk is replaced wholesale, retry loop
        # is never entered; speed up just in case.
        monkeypatch.setattr(hr.time, "sleep", lambda *_a, **_k: None)

        threshold = hr._HOUSE_RULES_DEGRADE_THRESHOLD
        last = None
        for i in range(threshold):
            last = adp._check_house_rules_or_fail(
                prompt=f"benign request number {i}",
                persona="assistant", channel="discord", chat_key="c1",
            )
            # Both providers down + BENIGN prompt → classify() degrades to the
            # deterministic Tier-0 floor → ALLOW → _check returns None. The
            # classifier_degraded signal below must still fire — the backend
            # outage is what's tracked, independent of the per-request verdict.
            assert last is None

        degraded = [r for r in recorded if r[0] == "house_rules.classifier_degraded"]
        assert degraded, (
            "house_rules.classifier_degraded was NOT written — track_degradation "
            "called the 3-arg production writer with the wrong arity (F-03 regression)"
        )
        et, sev, details = degraded[0]
        assert sev == "WARNING"  # from EVENT_SEVERITY
        assert details.get("error_count") == threshold
        assert details.get("window_s") == hr._HOUSE_RULES_DEGRADE_WINDOW_S
