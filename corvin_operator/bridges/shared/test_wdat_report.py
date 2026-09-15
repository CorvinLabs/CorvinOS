"""Tests for wdat_report.py — ADR-0109 M5."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from . import wdat_report as _wr
except ImportError:
    import wdat_report as _wr  # type: ignore[no-redef]

if _wr._FORGE_PATH not in sys.path:
    sys.path.insert(0, _wr._FORGE_PATH)


# ---------------------------------------------------------------------------
# _iter_audit_events
# ---------------------------------------------------------------------------

def _make_event(event_type: str, details: dict) -> str:
    return json.dumps({"event_type": event_type, "details": details})


def test_iter_audit_events_filters_by_type():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(_make_event("acs.worker_spawned", {"worker_id": "w1", "run_id": "r1"}) + "\n")
        f.write(_make_event("acs.manager_decided", {"run_id": "r1"}) + "\n")
        f.write(_make_event("unrelated.event", {"foo": "bar"}) + "\n")
        path = Path(f.name)
    try:
        events = _wr._iter_audit_events(path, frozenset({"acs.worker_spawned"}))
        assert len(events) == 1
        assert events[0]["event_type"] == "acs.worker_spawned"
    finally:
        path.unlink()


def test_iter_audit_events_missing_file():
    events = _wr._iter_audit_events(Path("/nonexistent/audit.jsonl"), frozenset({"acs.worker_spawned"}))
    assert events == []


def test_iter_audit_events_malformed_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("not json\n")
        f.write(_make_event("acs.worker_spawned", {"worker_id": "w2", "run_id": "r1"}) + "\n")
        path = Path(f.name)
    try:
        events = _wr._iter_audit_events(path, frozenset({"acs.worker_spawned"}))
        assert len(events) == 1
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# _build_tree
# ---------------------------------------------------------------------------

def _mock_events(run_id: str) -> list[dict]:
    nonce = "nonce-abc"
    return [
        {
            "event_type": "acs.manager_decided",
            "details": {
                "run_id": run_id, "iteration": 1,
                "decision_type": "DELEGATE", "decision_hash": "abc123",
                "n_subtasks": 2, "model_id": "claude-haiku-4-5", "spawn_nonce": nonce,
            },
        },
        {
            "event_type": "acs.worker_spawned",
            "details": {
                "run_id": run_id, "worker_id": "w1", "iteration": 1, "depth": 0,
                "engine_id": "claude_code", "model_id": "claude-haiku-4-5",
                "instruction_hash": "hash-prompt-1", "spawn_nonce": nonce,
                "parent_worker_id": None, "can_delegate": False,
            },
        },
        {
            "event_type": "acs.worker_spawned",
            "details": {
                "run_id": run_id, "worker_id": "w2", "iteration": 1, "depth": 0,
                "engine_id": "claude_code", "model_id": "claude-haiku-4-5",
                "instruction_hash": "hash-prompt-2", "spawn_nonce": nonce,
                "parent_worker_id": None, "can_delegate": False,
            },
        },
        {
            "event_type": "acs.worker_traced",
            "details": {
                "worker_id": "w1", "status": "success", "confidence": 0.9,
                "output_hash": "hash-out-1", "duration_ms": 1200, "tokens_used": 300,
                "spawn_nonce": nonce,
                "engine_attestation": {"engine_id": "claude_code", "model_id": "claude-haiku-4-5-20251001", "locality": "eu_cloud"},
            },
        },
        {
            "event_type": "acs.worker_traced",
            "details": {
                "worker_id": "w2", "status": "success", "confidence": 0.8,
                "output_hash": "hash-out-2", "duration_ms": 900, "tokens_used": 250,
                "spawn_nonce": nonce,
                "engine_attestation": {"engine_id": "claude_code", "model_id": "claude-haiku-4-5-20251001", "locality": "eu_cloud"},
            },
        },
    ]


def test_build_tree_two_workers():
    events = _mock_events("run-001")
    tree = _wr._build_tree(events, "run-001")
    assert len(tree["workers"]) == 2
    assert len(tree["manager_decisions"]) == 1
    assert "nonce-abc" in tree["nonce_groups"]
    assert set(tree["nonce_groups"]["nonce-abc"]) == {"w1", "w2"}


def test_build_tree_filters_by_run_id():
    events = _mock_events("run-001") + _mock_events("run-002")
    tree = _wr._build_tree(events, "run-001")
    # only workers from run-001 (w1, w2); run-002 also has w1/w2 but different run context
    # worker_traced has no run_id so they're matched by wid presence in spawned-set
    # for run-001, spawned-set is {w1,w2} — both traced events match
    assert len(tree["manager_decisions"]) == 1
    assert tree["manager_decisions"][0]["run_id"] == "run-001"


# ---------------------------------------------------------------------------
# Encryption / decryption round-trip
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    key = os.urandom(32)
    plaintext = b'{"hello": "world"}'
    encrypted = _wr._decrypt_trace.__module__  # ensure import path OK
    # Use acs_runtime helpers via import
    try:
        import acs_runtime as _rt
        enc = _rt._wdat_encrypt_content(plaintext, key)
        result = _wr._decrypt_trace(enc, key)
        assert result == {"hello": "world"}
    except ImportError:
        pytest.skip("acs_runtime not importable")


def test_decrypt_wrong_key_returns_none():
    key1 = os.urandom(32)
    key2 = os.urandom(32)
    plaintext = b'{"test": 1}'
    try:
        import acs_runtime as _rt
        enc = _rt._wdat_encrypt_content(plaintext, key1)
        result = _wr._decrypt_trace(enc, key2)
        assert result is None
    except ImportError:
        pytest.skip("acs_runtime not importable")


def test_decrypt_too_short_returns_none():
    result = _wr._decrypt_trace(b"short", os.urandom(32))
    assert result is None


# ---------------------------------------------------------------------------
# _real_chain_integrity — adversarial review finding: this used to be
# cosmetic ("verified" meant only "this run has >=1 event", never an actual
# hash-chain walk). Now performs a real verify_chain() call.
# ---------------------------------------------------------------------------

def test_chain_integrity_verified_for_a_real_intact_chain():
    from forge import security_events as _sec  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        _sec.write_event(audit_path, "acs.manager_decided", run_id="r1", details={"run_id": "r1"})
        _sec.write_event(audit_path, "acs.worker_spawned", run_id="r1", details={"run_id": "r1", "worker_id": "w1"})
        assert _wr._real_chain_integrity(audit_path, run_event_count=2) == "verified"


def test_chain_integrity_empty_when_no_events_for_this_run():
    from forge import security_events as _sec  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        _sec.write_event(audit_path, "acs.manager_decided", run_id="other-run", details={"run_id": "other-run"})
        # Chain itself is intact, but THIS run has 0 matching events.
        assert _wr._real_chain_integrity(audit_path, run_event_count=0) == "empty"


def test_chain_integrity_broken_when_chain_is_tampered():
    """The core regression: a tampered audit file must show "broken", not a
    permanent cosmetic "verified" just because the run had events."""
    from forge import security_events as _sec  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        _sec.write_event(audit_path, "acs.manager_decided", run_id="r1", details={"run_id": "r1"})
        _sec.write_event(audit_path, "acs.worker_spawned", run_id="r1", details={"run_id": "r1", "worker_id": "w1"})

        # Tamper: flip a character in the first record's details, invalidating
        # its stored hash without touching the hash field itself.
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["details"]["run_id"] = "TAMPERED"
        lines[0] = json.dumps(rec)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Even though this run has events (run_event_count > 0), a broken
        # chain must never report "verified".
        assert _wr._real_chain_integrity(audit_path, run_event_count=2) == "broken"


def test_chain_integrity_unavailable_when_verifier_cannot_be_reached():
    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        audit_path.write_text("{}\n")
        with patch.object(_wr, "_FORGE_PATH", "/nonexistent/path/that/breaks/import"), \
             patch("builtins.__import__", side_effect=ImportError("boom")):
            assert _wr._real_chain_integrity(audit_path, run_event_count=1) == "unavailable"


def test_generate_report_reflects_broken_chain_end_to_end():
    from forge import security_events as _sec  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        for ev in _mock_events("run-001"):
            _sec.write_event(
                audit_path, ev["event_type"], run_id="run-001", details=ev.get("details", {}),
            )
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["details"] = {**rec.get("details", {}), "tampered": True}
        lines[0] = json.dumps(rec)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch.object(_wr, "_audit_path", return_value=audit_path):
            report = _wr.generate_report("run-001", tenant_id="_test")
    assert report["chain_integrity"] == "broken"


# ---------------------------------------------------------------------------
# generate_report integration
# ---------------------------------------------------------------------------

def test_generate_report_no_workers():
    """Report with no matching events should still produce valid structure."""
    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        audit_path.write_text("")
        with patch.object(_wr, "_audit_path", return_value=audit_path):
            report = _wr.generate_report("run-xyz", tenant_id="_test")
    assert report["run_id"] == "run-xyz"
    assert report["total_workers"] == 0
    assert "eu_ai_act" in report
    assert "delegation_tree" in report


def test_generate_report_with_events():
    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        lines = [json.dumps(ev) + "\n" for ev in _mock_events("run-001")]
        audit_path.write_text("".join(lines))
        with patch.object(_wr, "_audit_path", return_value=audit_path):
            report = _wr.generate_report("run-001", tenant_id="_test")
    assert report["total_workers"] == 2
    assert report["total_manager_decisions"] == 1
    assert len(report["workers"]) == 2
    w1 = next(w for w in report["workers"] if w["worker_id"] == "w1")
    assert w1["status"] == "success"
    assert w1["confidence"] == 0.9
    assert w1["engine"] == "claude-haiku-4-5-20251001"
    assert report["eu_ai_act"]["art_13_transparency"] == "full"


def test_generate_report_include_content_no_key():
    """--include-content with no key: content field is None, no crash."""
    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        lines = [json.dumps(ev) + "\n" for ev in _mock_events("run-001")]
        audit_path.write_text("".join(lines))
        # Set run_dir so traces dir lookup succeeds (matches acs_runtime session path)
        run_dir = Path(td) / "tenants" / "_test" / "sessions" / "discord:test" / "acs" / "runs" / "run-001"
        run_dir.mkdir(parents=True)
        (run_dir / "traces").mkdir()
        with patch.object(_wr, "_audit_path", return_value=audit_path), \
             patch.object(_wr, "_corvin_home", return_value=Path(td)), \
             patch.object(_wr, "_wdat_load_key", return_value=None):
            report = _wr.generate_report("run-001", tenant_id="_test", include_content=True)
    # No key → no content (load_trace returns None for encrypted, None for missing plaintext)
    for w in report["workers"]:
        assert "content" in w
        assert w["content"] is None


# ---------------------------------------------------------------------------
# CLI arg parsing (no subprocess needed)
# ---------------------------------------------------------------------------

def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        _wr.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "run_id" in out


def test_cli_output_to_file():
    with tempfile.TemporaryDirectory() as td:
        audit_path = Path(td) / "audit.jsonl"
        audit_path.write_text("")
        out_path = Path(td) / "report.json"
        with patch.object(_wr, "_audit_path", return_value=audit_path):
            _wr.main(["run-abc", "--tenant", "_test", "--output", str(out_path)])
        report = json.loads(out_path.read_text())
    assert report["run_id"] == "run-abc"
    assert report["total_workers"] == 0
