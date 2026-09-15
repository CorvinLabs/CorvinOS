"""ADR-0129 M1/M2 + F-A4 (2026-09-07) — structural audit-detail floor.

Verifies that the chain writer drops content / PII / secret / oversize
fields from every event's `details`, that the floor is DEFAULT-DENY for
keys (an event type without a registered allowlist admits only the
universal metadata vocabulary ``_AUDIT_KNOWN_KEYS``), and that string
values on such events are scanned for email/phone shapes — while legit
metadata is preserved (false-positive avoidance is load-bearing).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.security_events import (  # noqa: E402
    _AUDIT_KNOWN_KEYS, filter_audit_details, write_event, verify_chain,
)

import pytest  # noqa: E402


# ── unit: forbidden keys dropped ──────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "prompt", "output", "text", "transcript", "message", "content", "body",
    "instruction", "payload", "query", "email", "password", "secret", "token",
    "api_key", "access_token", "rows", "stdout", "stderr",
])
def test_forbidden_exact_key_dropped(key):
    cleaned, dropped = filter_audit_details({key: "leaked-value", "count": 1})
    assert key not in cleaned
    assert key in dropped
    assert cleaned.get("count") == 1
    # the value is NEVER retained anywhere
    assert "leaked-value" not in json.dumps(cleaned)


@pytest.mark.parametrize("key", [
    "db_password", "client_secret", "api_credential", "csrf_token",
    "authorization", "session_cookie", "user_passphrase", "x_api_key",
])
def test_forbidden_substring_key_dropped(key):
    cleaned, dropped = filter_audit_details({key: "xyz"})
    assert key in dropped and key not in cleaned


# ── unit: legit metadata PRESERVED (no false positives) ───────────────────

@pytest.mark.parametrize("key", [
    "text_len", "output_hash", "instruction_hash", "rows_sampled",
    "tokens_used", "input_tokens", "output_tokens", "error_message",
    "reason", "sid_fingerprint", "matched_rule", "query_latency_ms",
    "query_count", "tenant_id", "engine_id", "classification", "uid_hash",
    "audit_ref", "task_id", "session_id", "skill_id", "plugin_id", "lom", "lom_hash",
])
def test_legit_metadata_preserved(key):
    cleaned, dropped = filter_audit_details({key: 42})
    assert key in cleaned, f"false positive: {key} was dropped"
    assert dropped == []


# ── F-A4: default-deny vocabulary for unregistered event types ────────────

def test_unregistered_event_drops_unknown_key_and_names_it():
    cleaned, dropped = filter_audit_details(
        {"some_metric": 7, "count": 7, "prompt": "leak"}, event_type="random.event")
    assert "some_metric" not in cleaned and "some_metric" in dropped
    assert cleaned["count"] == 7
    assert "prompt" in dropped
    assert cleaned["_dropped_fields"] == ["prompt", "some_metric"]


def test_vocabulary_has_no_content_or_pii_shaped_names():
    for bad in ("prompt", "text", "message", "email", "password", "snippet",
                "line_excerpt", "userName", "request", "subject_id"):
        assert bad not in _AUDIT_KNOWN_KEYS, bad


@pytest.mark.parametrize("value", [
    "alice@example.com",
    "contact: Alice.Bauer+news@sub.example.co.uk today",
    "+49 170 1234567",
    "call 0170-1234567 now",
    "(030) 123 45 67",
])
def test_pii_value_under_benign_key_dropped_on_unregistered_event(value):
    cleaned, dropped = filter_audit_details({"reason": value, "count": 1},
                                            event_type="random.event")
    assert "reason" in dropped and "reason" not in cleaned, value
    assert value not in json.dumps(cleaned)
    assert cleaned["count"] == 1


@pytest.mark.parametrize("value", [
    "4915123@s.whatsapp.net",                # WhatsApp JID
    "123456789012@g.us",                     # WhatsApp group JID
    "https://user@host.example/path",        # URL userinfo
    "rejected by user@team",                 # no TLD
    "2026-09-07T01:40:00",                   # timestamp
    "2026-09-07",                            # date
    "123456789012345678",                    # snowflake id (no + / separators)
    "0.10.112",                              # version
    "00123456789abcdef0123",                 # hex-ish id
    "run-0123-4567",                         # short id
])
def test_pseudonymous_ids_and_structured_values_preserved(value):
    cleaned, dropped = filter_audit_details({"target": value}, event_type="random.event")
    assert cleaned.get("target") == value, value
    assert dropped == []


def test_pii_scan_applies_recursively_under_known_key():
    cleaned, _ = filter_audit_details(
        {"extra": {"note": "bob@example.com", "count": 2}}, event_type="random.event")
    assert "bob@example.com" not in json.dumps(cleaned)
    assert cleaned["extra"]["count"] == 2


def test_pii_scan_not_applied_to_allowlisted_events():
    # A registered allowlist is a maintainer-vetted field set; its values are
    # not second-guessed by the shape scan (only the denylist + size floor).
    from forge.security_events import register_event_allowlist
    register_event_allowlist("vetted.evt", {"host"})
    cleaned, dropped = filter_audit_details({"host": "smtp@mail.example.com"},
                                            event_type="vetted.evt")
    assert cleaned["host"] == "smtp@mail.example.com" and dropped == []


# ── unit: oversize value dropped ──────────────────────────────────────────

def test_oversize_value_dropped():
    big = "x" * 5000
    cleaned, dropped = filter_audit_details({"detail": big, "count": 3})
    assert "detail" in dropped and "detail" not in cleaned
    assert cleaned.get("count") == 3
    assert big not in json.dumps(cleaned)


def test_oversize_nested_blob_dropped():
    # Recursion cleans in place: the oversize nested "rows" is dropped, the
    # "extra" container survives — the blob value must be absent either way.
    cleaned, _ = filter_audit_details({"extra": {"rows": ["y" * 3000]}})
    assert "y" * 3000 not in json.dumps(cleaned)
    assert "rows" not in cleaned.get("extra", {})


def test_short_hash_preserved():
    h = "a" * 64
    cleaned, _ = filter_audit_details({"output_hash": h})
    assert cleaned["output_hash"] == h


# ── unit: marker + unfiltered ─────────────────────────────────────────────

def test_dropped_fields_marker_lists_keys_not_values():
    cleaned, _ = filter_audit_details({"prompt": "secret text", "count": 1})
    assert cleaned["_dropped_fields"] == ["prompt"]
    assert "secret text" not in json.dumps(cleaned)


def test_unfiltered_bypasses():
    d = {"prompt": "kept-when-unfiltered"}
    cleaned, dropped = filter_audit_details(d, unfiltered=True)
    assert cleaned == d and dropped == []


def test_never_raises_on_weird_input():
    for bad in (None, "string", 123, [], {"k": object()}):
        filter_audit_details(bad)  # must not raise


# ── integration: floor applies through write_event + chain stays valid ────

def test_write_event_applies_floor_and_chain_verifies():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        write_event(path, "test.event", severity="INFO",
                    details={"prompt": "LEAK", "tokens_used": 5, "reason": "ok",
                             "adhoc_key": "x"})
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        assert "prompt" not in rec["details"]
        assert "adhoc_key" not in rec["details"]
        assert rec["details"]["tokens_used"] == 5
        assert rec["details"]["reason"] == "ok"
        assert rec["details"]["_dropped_fields"] == ["adhoc_key", "prompt"]
        assert "LEAK" not in path.read_text()
        ok, problems = verify_chain(path)
        assert ok, problems


def test_write_event_unfiltered_keeps_field():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        write_event(path, "test.event", details={"output_hash": "h" * 64},
                    unfiltered=True)
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        assert rec["details"]["output_hash"] == "h" * 64


def test_write_event_binds_lom_hash():
    # ADR-0537 / F-A17: a record carrying a LoM always carries its binding.
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        write_event(path, "test.event",
                    details={"lom": "operator/forge/forge/security_events.py:write_event"})
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        assert len(rec["details"]["lom_hash"]) == 64
        ok, problems = verify_chain(path)
        assert ok, problems


# ── M2: per-event positive allowlist ──────────────────────────────────────

def test_allowlisted_event_drops_unknown_key():
    # acs.datasource_snapshot has a registered allowlist; an extra key is
    # dropped even though it isn't on the denylist.
    cleaned, dropped = filter_audit_details(
        {"datasource": "db1", "adapter": "postgresql", "rogue_field": "x"},
        event_type="acs.datasource_snapshot",
    )
    assert cleaned["datasource"] == "db1"
    assert cleaned["adapter"] == "postgresql"
    assert "rogue_field" not in cleaned
    assert "rogue_field" in dropped


def test_allowlisted_event_keeps_all_declared_fields():
    d = {"run_id": "r1", "datasource": "db1", "adapter": "postgresql",
         "classification": "INTERNAL", "snapshot_taken": True,
         "snapshot_bytes": 120, "withheld_sensitive": False}
    cleaned, dropped = filter_audit_details(dict(d), event_type="acs.datasource_snapshot")
    assert dropped == []
    for k in d:
        assert cleaned[k] == d[k]


def test_register_event_allowlist():
    from forge.security_events import register_event_allowlist
    register_event_allowlist("custom.evt", {"a", "b"})
    cleaned, dropped = filter_audit_details({"a": 1, "c": 2}, event_type="custom.evt")
    assert cleaned["a"] == 1 and "c" in dropped


# ── review fixes: nested bypass, marker forgery, value secrets, fail-closed ──

def test_nested_dict_forbidden_key_dropped():
    cleaned, _ = filter_audit_details({"extra": {"prompt": "SECRET-LEAK", "count": 1}})
    assert "SECRET-LEAK" not in json.dumps(cleaned)
    assert cleaned["extra"]["count"] == 1
    assert "prompt" not in cleaned["extra"]


def test_nested_list_secret_dropped():
    cleaned, _ = filter_audit_details(
        {"entries": [{"secret": "sk-leak"}, {"id": 7}]})
    assert "sk-leak" not in json.dumps(cleaned)
    assert {"id": 7} in cleaned["entries"]


def test_deeply_nested_forbidden_dropped():
    cleaned, _ = filter_audit_details(
        {"extra": {"b": {"c": {"password": "DEEP-SECRET-VALUE"}}}})
    # the VALUE is gone; the key NAME may appear in a _dropped_fields marker.
    assert "DEEP-SECRET-VALUE" not in json.dumps(cleaned)
    assert cleaned["extra"]["b"]["c"].get("password") is None


def test_caller_supplied_markers_stripped():
    cleaned, _ = filter_audit_details({"_unfiltered": True,
                                       "_dropped_fields": ["fake"], "count": 1})
    # caller-forged markers removed; no real drop happened beyond them
    assert cleaned.get("_unfiltered") is None
    assert cleaned.get("count") == 1
    # _dropped_fields, if present, is the writer's own (lists the stripped markers)
    assert "fake" not in cleaned.get("_dropped_fields", [])


def test_secret_value_under_benign_key_dropped():
    # Only UNAMBIGUOUS credential tokens are scanned in values.
    for leak in ("sk-abcdefghijklmnopqrstuvwx",
                 "AKIAIOSFODNN7EXAMPLE",
                 "ghp_abcdefghijklmnopqrstuvwxyz0123",
                 "-----BEGIN RSA PRIVATE KEY-----"):
        cleaned, dropped = filter_audit_details({"reason": leak})
        assert "reason" in dropped, leak
        assert leak not in json.dumps(cleaned)


def test_benign_short_value_kept():
    cleaned, dropped = filter_audit_details({"reason": "all good", "count": 5})
    assert cleaned["reason"] == "all good" and dropped == []


def test_structural_fields_survive_allowlist():
    from forge.security_events import register_event_allowlist
    register_event_allowlist("bridge.evt", {"thing"})
    cleaned, dropped = filter_audit_details(
        {"thing": 1, "channel": "discord", "chat_key": "c1",
         "user": "u1", "persona": "assistant", "rogue": "x"},
        event_type="bridge.evt")
    for sf in ("channel", "chat_key", "user", "persona", "thing"):
        assert sf in cleaned, sf
    assert "rogue" in dropped


def test_circular_reference_bounded_failclosed():
    # A circular reference must NOT cause RecursionError / unbounded output —
    # the depth bound + fail-closed serialization check breaks the cycle.
    circular: dict = {}
    circular["self"] = circular
    cleaned, _ = filter_audit_details({"extra": circular})
    s = json.dumps(cleaned)  # must serialise (cycle broken) without raising
    assert len(s) < 10_000  # bounded, not infinite


def test_unserialisable_scalar_failclosed():
    # A scalar whose serialization raises (even via default=str) is dropped
    # (fail-closed, review #6).
    class Bad:
        def __str__(self): raise RuntimeError("boom")
        def __repr__(self): raise RuntimeError("boom")
    cleaned, dropped = filter_audit_details({"reason": Bad()})
    assert "reason" in dropped
    assert "reason" not in cleaned


def test_write_event_strips_forged_unfiltered_marker():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        write_event(path, "test.x", details={"_unfiltered": True, "count": 1})
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        # normal (filtered) write: forged marker must be gone
        assert rec["details"].get("_unfiltered") is None
        assert rec["details"]["count"] == 1


# ── round-4 review: writer/filter serialization symmetry + depth fail-closed ──

@pytest.mark.parametrize("val", [
    {"a", "b"},                       # set — not JSON-serializable
    b"some-bytes-value",              # bytes — not JSON-serializable
    {"sk-abcdefghijklmnopqrstuvwx"},  # set wrapping a credential token
    b"ghp_abcdefghijklmnopqrstuvwxyz0123",  # bytes wrapping a token
])
def test_unserialisable_value_dropped_failclosed(val):
    # default=str would make these look "short and fine"; the writer cannot
    # serialize them. Filter must drop (fail-closed), never keep/leak.
    cleaned, dropped = filter_audit_details({"reason": val})
    assert "reason" in dropped and "reason" not in cleaned
    assert "sk-" not in json.dumps(cleaned) and "ghp_" not in json.dumps(cleaned)


def test_depth_bound_forbidden_short_value_dropped():
    # 7 levels deep, a forbidden key with a SHORT value: previously survived
    # (depth fail-open). Now the over-deep subtree is dropped wholesale.
    deep = {"entries": {"b": {"c": {"d": {"e": {"f": {"g": {"password": "x"}}}}}}}}
    cleaned, _ = filter_audit_details(deep)
    assert "x" not in json.dumps(cleaned)
    assert "password" not in json.dumps(cleaned)


def test_non_str_and_mixed_keys_do_not_crash_writer():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        # mixed int+str keys make sort_keys json.dumps raise; exotic tuple key
        # likewise. Writer must not crash and the chain must verify.
        write_event(path, "test.keys", details={1: "a", "name": "c", (1, 2): "d"})
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        assert rec["details"].get("name") == "c"
        ok, problems = verify_chain(path)
        assert ok, problems


def test_write_event_survives_unserialisable_details():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audit.jsonl"
        # never-raise contract: set/bytes values must not crash the write.
        write_event(path, "test.weird",
                    details={"skills": {"x", "y"}, "bundle": b"raw", "count": 1})
        rec = json.loads(path.read_text().strip().splitlines()[-1])
        assert rec["details"]["count"] == 1
        assert "skills" not in rec["details"] and "bundle" not in rec["details"]
        ok, problems = verify_chain(path)
        assert ok, problems


# ── ADR-0152: count-map preservation (PII-class metric / GDPR ROPA spine) ──

def test_pii_detected_classes_countmap_preserved():
    # The data.pii_detected ``classes`` count-map must survive the floor with
    # the "email" class label intact (it collides with the M1 key denylist).
    out, dropped = filter_audit_details(
        {"data_handle": "h1", "classes": {"email": 4, "phone": 2}},
        event_type="data.pii_detected",
    )
    assert out["classes"] == {"email": 4, "phone": 2}
    assert "classes" not in dropped


def test_pii_detected_sentinel_label_preserved():
    out, _ = filter_audit_details(
        {"data_handle": "h1", "classes": {"<no_pii>": 5, "email": 1}},
        event_type="data.pii_detected",
    )
    assert out["classes"] == {"<no_pii>": 5, "email": 1}


def test_countmap_gate_rejects_unsafe_shapes():
    # SECURITY: the count-map exemption GATE must reject anything that is not a
    # strict {short-lowercase-label: non-negative-int} map, so no PII value, no
    # credential token, and no free-text key can ride the verbatim-preserve path.
    from forge.security_events import _is_safe_count_map
    assert _is_safe_count_map({"email": 4, "phone": 2}) is True
    assert _is_safe_count_map({"<no_pii>": 5}) is True
    assert _is_safe_count_map({"john@example.com": 1}) is False   # @-bearing key
    assert _is_safe_count_map({"email": "sk-abcdefghijklmnopqrst"}) is False  # token value
    assert _is_safe_count_map({"email": -1}) is False             # negative count
    assert _is_safe_count_map({"email": True}) is False           # bool, not int
    assert _is_safe_count_map({"Has Space": 1}) is False          # free-text key
    assert _is_safe_count_map({}) is False                        # empty → normal path


def test_countmap_credential_value_not_preserved():
    # A credential token smuggled as a count-map VALUE must not survive: the
    # gate rejects the map (non-int value) → normal scrub drops the forbidden key.
    out, _ = filter_audit_details(
        {"data_handle": "h1", "classes": {"email": "sk-abcdefghijklmnopqrstuvwx"}},
        event_type="data.pii_detected",
    )
    assert "sk-" not in json.dumps(out)


def test_countmap_rejects_non_int_values():
    # A non-count value means it is not a count-map → normal scrub, which drops
    # the forbidden "email" key rather than preserving it.
    out, _ = filter_audit_details(
        {"data_handle": "h1", "classes": {"email": "leaked@x.com"}},
        event_type="data.pii_detected",
    )
    assert "email" not in out.get("classes", {})


def test_countmap_only_for_registered_event():
    # The same shape on an UNREGISTERED event gets no exemption.
    out, _ = filter_audit_details(
        {"classes": {"email": 4}}, event_type="some.other_event")
    assert "email" not in out.get("classes", {})


def test_webhook_secret_ref_name_preserved():
    # secret_ref is a vault key NAME (collides with the "secret" substring
    # denylist); the positive allowlist must let it through for this event.
    out, dropped = filter_audit_details(
        {"run_id": "r1", "secret_ref": "discord_hmac", "host": "example.com"},
        event_type="gateway.webhook_secret_missing",
    )
    assert out["secret_ref"] == "discord_hmac"
    assert "secret_ref" not in dropped


# ── R3 follow-up: the reserved structural spine is scanned, not exempt ──────
#
# ``user`` / ``chat_key`` / ``channel`` / ``persona`` / ``tenant_id`` are exempt
# from the KEY filters (they are what makes a record attributable, GDPR Art. 30).
# They used to be exempt from the VALUE scan too, justified as "these carry
# identifiers, not free text". Measured against the live chains that was false:
# of 596 039 records, 2 962 ``user`` and 2 580 ``chat_key`` values carried an
# email or phone shape — the email/messenger bridges pass the sender through and
# ``adapter._pii_fp`` only covers the adapter's own emitter. A raw email in a
# hash-chained, append-only, never-redactable file is exactly what the floor
# exists to stop.

import hashlib  # noqa: E402


def _fp(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()[:8]


@pytest.mark.parametrize("key", ["user", "chat_key"])
@pytest.mark.parametrize("value", [
    "notifications@github.com", "alice@company.com", "+491234567890",
    "failed-payments+acct_1rhzepjkh2g7hnv5@stripe.com",
])
def test_reserved_key_with_a_pii_shape_is_fingerprinted(key, value):
    out, dropped = filter_audit_details({key: value}, event_type="bridge.login")
    assert out[key] == _fp(value), out
    assert value not in json.dumps(out)
    assert key in out["_pii_fingerprinted"]
    # FINGERPRINTED, never dropped: an audit record with the actor removed is
    # not a safer trail, it is an unusable one (Art. 30 attribution).
    assert key not in dropped


def test_the_fingerprint_matches_the_adapter_s_own():
    """One pseudonym namespace: a value the adapter already redacted and one the
    floor redacts must collide, or the same person reads as two."""
    sys.path.insert(0, str(ROOT.parents[1] / "operator" / "bridges" / "shared"))
    from adapter import _pii_fp  # type: ignore[import]

    out, _ = filter_audit_details({"user": "alice@company.com"},
                                  event_type="bridge.login")
    assert out["user"] == _pii_fp("alice@company.com")


@pytest.mark.parametrize("key,value", [
    ("user", "97811003"), ("user", "user-1"), ("chat_key", "b27996da"),
    ("channel", "discord"), ("persona", "assistant"), ("tenant_id", "_default"),
    ("user", ""), ("channel", ""),
])
def test_identifier_shapes_actually_in_use_are_untouched(key, value):
    """Bounds the rule with the values the live chains really carry — a scan
    that mangles ordinary ids would break every correlation the trail exists
    for."""
    out, dropped = filter_audit_details({key: value}, event_type="bridge.login")
    assert out[key] == value, out
    assert "_pii_fingerprinted" not in out
    assert not dropped


def test_the_marker_cannot_be_forged_by_a_caller():
    """``_pii_fingerprinted`` is a WRITER-only marker, like ``_dropped_fields``:
    a caller that supplies it must not be able to claim a redaction that never
    happened. Denied on input, re-injected by the writer."""
    out, dropped = filter_audit_details(
        {"user": "alice@company.com", "_pii_fingerprinted": ["forged"]},
        event_type="bridge.login")
    assert "_pii_fingerprinted" in dropped
    assert out["_pii_fingerprinted"] == ["user"]


def test_a_pii_reserved_value_never_reaches_the_chain_file():
    """Through the real writer, not the filter: the record on disk must not
    contain the address anywhere."""
    tmp = Path(tempfile.mkdtemp(prefix="reserved-pii-")) / "audit.jsonl"
    rec = write_event(tmp, "bridge.login",
                      details={"user": "alice@company.com", "channel": "email"})
    assert rec["details"]["user"] == _fp("alice@company.com")
    raw = tmp.read_text(encoding="utf-8")
    assert "alice@company.com" not in raw
    ok, problems = verify_chain(tmp)
    assert ok, problems


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
