"""Phase 12.5 E2E — MCP-tool handlers + DataRegistry.

Covers:
  * DataRegistry: register → get → list → update_last_snapshot → delete
  * Handle shape validation (is_handle_shape)
  * file_hash determinism / changes
  * call_data_register: full pipeline (sniff → register → snapshot →
    pii-detect → redact → audit)
  * call_data_snapshot: re-snapshot with new options
  * call_data_unregister: idempotent delete + audit
  * audit-event details carry NO raw values (metadata only)
  * malformed args raise ToolError cleanly
  * unknown handle returns clean error
  * sensitive values are redacted in the snapshot the LLM sees

Self-contained — tempfiles + in-memory audit hook.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.corvin_data import (  # noqa: E402
    DataPolicy,
    DataRegistry,
    HandleNotFound,
    HandleStoreError,
    ToolError,
    call_data_register,
    call_data_snapshot,
    call_data_unregister,
    compute_file_hash,
    is_handle_shape,
    new_handle,
)


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _write_csv(content: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(content)
    fh.close()
    return Path(fh.name)


# ---------------------------------------------------------------------------
# Handle shape + helpers
# ---------------------------------------------------------------------------

def test_new_handle_shape():
    print("\n[handle: new_handle() produces valid shape]")
    for _ in range(5):
        h = new_handle()
        t(f"shape valid: {h}", is_handle_shape(h))


def test_is_handle_shape_rejects_bad():
    print("\n[handle: is_handle_shape rejects malformed]")
    for bad in ["data_short", "run_abc123", "", "data_" + "x" * 50, None, 123]:
        t(f"rejects: {bad!r}", not is_handle_shape(bad))  # type: ignore[arg-type]


def test_compute_file_hash_deterministic():
    print("\n[hash: same file → same hash]")
    p = _write_csv("a,b,c\n1,2,3\n4,5,6\n")
    try:
        h1 = compute_file_hash(p)
        h2 = compute_file_hash(p)
        t("deterministic", h1 == h2)
        t("format prefix", h1.startswith("sha256:"))
    finally:
        p.unlink()


def test_compute_file_hash_changes():
    print("\n[hash: edited file → different hash]")
    p = _write_csv("a,b,c\n1,2,3\n")
    try:
        h1 = compute_file_hash(p)
        p.write_text("a,b,c\n1,2,3\n4,5,6\n")
        h2 = compute_file_hash(p)
        t("different", h1 != h2)
    finally:
        p.unlink()


# ---------------------------------------------------------------------------
# DataRegistry CRUD
# ---------------------------------------------------------------------------

def test_registry_register_returns_record():
    print("\n[registry: register returns DataHandle record]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a,b\n1,2\n")
        try:
            rec = reg.register(path=p, fmt="csv", registered_by="tester")
            t("handle valid", is_handle_shape(rec.handle))
            t("path correct", rec.path == str(p.resolve()))
            t("format set", rec.format == "csv")
            t("registered_by", rec.registered_by == "tester")
            t("file_hash present", rec.file_hash.startswith("sha256:"))
        finally:
            p.unlink()


def test_registry_get_roundtrip():
    print("\n[registry: register → get round-trip]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a\n1\n")
        try:
            rec = reg.register(path=p, fmt="csv")
            retrieved = reg.get(rec.handle)
            t("same handle", retrieved.handle == rec.handle)
            t("same path", retrieved.path == rec.path)
            t("same format", retrieved.format == rec.format)
        finally:
            p.unlink()


def test_registry_get_unknown_raises():
    print("\n[registry: get(unknown) raises HandleNotFound]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            # Syntactically valid handle that's not in the store
            reg.get(new_handle())
            t("raised HandleNotFound", False, detail="should have raised")
        except HandleNotFound:
            t("raised HandleNotFound", True)


def test_registry_malformed_handle_rejected():
    print("\n[registry: malformed handle raises HandleStoreError]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            reg.get("not_a_handle")
            t("rejected", False)
        except HandleStoreError as e:
            t("rejected", "malformed" in str(e))


def test_registry_delete_idempotent():
    print("\n[registry: delete returns bool, idempotent]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a\n1\n")
        try:
            rec = reg.register(path=p, fmt="csv")
            t("first delete True", reg.delete(rec.handle) is True)
            t("second delete False", reg.delete(rec.handle) is False)
        finally:
            p.unlink()


def test_registry_list_returns_all():
    print("\n[registry: list returns all handles in order]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p1 = _write_csv("a\n1\n")
        p2 = _write_csv("b\n2\n")
        try:
            r1 = reg.register(path=p1, fmt="csv")
            r2 = reg.register(path=p2, fmt="csv")
            ls = reg.list()
            t("two handles listed", len(ls) == 2)
            handles = {r.handle for r in ls}
            t("both present", handles == {r1.handle, r2.handle})
        finally:
            p1.unlink()
            p2.unlink()


# ---------------------------------------------------------------------------
# call_data_register — full pipeline
# ---------------------------------------------------------------------------

class _AuditCollector:
    """Helper to capture audit-event emissions for inspection."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, details: dict) -> None:
        self.events.append((event_type, details))


def test_data_register_full_pipeline():
    print("\n[mcp: data_register runs full pipeline]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        # Write a CSV with PII columns
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        fh.write("customer_email,amount,country\n")
        fh.write("alice@example.com,10,DE\n")
        fh.write("bob@example.com,20,AT\n")
        fh.write("carol@example.com,30,CH\n")
        fh.close()
        p = Path(fh.name)
        audit = _AuditCollector()
        try:
            result = call_data_register(
                reg,
                {"path": str(p)},
                persona="tester",
                tenant_id="_default",
                audit=audit,
            )
            t("result has handle",
              "data_handle" in result and is_handle_shape(result["data_handle"]))
            t("result has snapshot", "snapshot" in result)
            snap = result["snapshot"]
            t("snapshot has file meta", "file" in snap)
            t("snapshot has schema", "schema" in snap)
            t("snapshot has sample", "sample" in snap)
            t("snapshot has stats", "stats" in snap)
            # Verify PII was REDACTED in the sample
            first = snap["sample"][0]
            t("email value redacted",
              first["customer_email"] == "<email>", detail=str(first))
            # Audit events emitted
            event_types = [e[0] for e in audit.events]
            t("data.registered emitted",
              "data.registered" in event_types, detail=str(event_types))
            t("data.pii_detected emitted",
              "data.pii_detected" in event_types)
            t("data.snapshot_generated emitted",
              "data.snapshot_generated" in event_types)
        finally:
            p.unlink()


def test_data_register_audit_carries_no_values():
    print("\n[mcp: audit details contain no raw PII]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        fh.write("email\nalice@private.example.com\nbob@private.example.com\n")
        fh.close()
        p = Path(fh.name)
        audit = _AuditCollector()
        try:
            call_data_register(reg, {"path": str(p)}, audit=audit)
            joined = json.dumps([dict(d) for _e, d in audit.events])
            t("no email value in audit",
              "private.example.com" not in joined,
              detail="found leaked PII")
            t("no column-name 'email' value listed verbatim in details",
              "alice@" not in joined and "bob@" not in joined)
        finally:
            p.unlink()


def test_data_register_missing_path_raises_toolerror():
    print("\n[mcp: missing path raises ToolError]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            call_data_register(reg, {})
            t("rejected", False, detail="should have raised")
        except ToolError as e:
            t("rejected", "path" in str(e))


def test_data_register_nonexistent_path():
    print("\n[mcp: nonexistent path raises ToolError]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            call_data_register(reg, {"path": "/no/such/file.csv"})
            t("rejected", False, detail="should have raised")
        except ToolError as e:
            t("rejected", "does not exist" in str(e))


def test_data_register_with_explicit_format():
    print("\n[mcp: explicit format override works]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        fh.write("a,b\n1,2\n3,4\n")
        fh.close()
        p = Path(fh.name)
        try:
            result = call_data_register(reg, {"path": str(p), "format": "csv"})
            t("registered as csv",
              result["snapshot"]["file"]["format"] == "csv")
        finally:
            p.unlink()


def test_data_register_snapshot_options_passthrough():
    print("\n[mcp: snapshot_options overrides applied]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        body = "\n".join(f"r{i}" for i in range(200))
        # Use non-PII header so values aren't redacted in the assertion below.
        p = _write_csv(f"item\n{body}\n")
        try:
            result = call_data_register(reg, {
                "path": str(p),
                "snapshot_options": {"rows": 5, "rows_strategy": "head"},
            })
            sample = result["snapshot"]["sample"]
            t("sample size 5", len(sample) <= 5, detail=str(len(sample)))
            t("head-only strategy starts at r0",
              sample[0]["item"] == "r0", detail=str(sample[0]))
        finally:
            p.unlink()


# ---------------------------------------------------------------------------
# call_data_snapshot — re-snapshot
# ---------------------------------------------------------------------------

def test_data_snapshot_after_register():
    print("\n[mcp: data_snapshot re-snapshots with new options]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        body = "\n".join(f"r{i}" for i in range(100))
        p = _write_csv(f"name\n{body}\n")
        audit = _AuditCollector()
        try:
            reg_result = call_data_register(reg, {"path": str(p)}, audit=audit)
            audit.events.clear()  # forget register events
            handle = reg_result["data_handle"]
            snap_result = call_data_snapshot(
                reg,
                {"data_handle": handle, "options": {"rows": 3, "rows_strategy": "head"}},
                audit=audit,
            )
            t("returns same handle", snap_result["data_handle"] == handle)
            t("smaller sample", len(snap_result["snapshot"]["sample"]) <= 3)
            event_types = [e[0] for e in audit.events]
            t("snapshot audit emitted",
              "data.snapshot_generated" in event_types)
        finally:
            p.unlink()


def test_data_snapshot_unknown_handle():
    print("\n[mcp: data_snapshot on unknown handle raises]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            call_data_snapshot(reg, {"data_handle": new_handle()})
            t("rejected", False, detail="should have raised")
        except ToolError as e:
            t("rejected", "unknown" in str(e))


def test_data_snapshot_malformed_handle():
    print("\n[mcp: malformed handle raises]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        try:
            call_data_snapshot(reg, {"data_handle": "not_a_handle"})
            t("rejected", False)
        except ToolError as e:
            t("rejected", "malformed" in str(e))


def test_data_snapshot_when_file_moved():
    print("\n[mcp: data_snapshot when file deleted → clean error]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a\n1\n")
        try:
            r = call_data_register(reg, {"path": str(p)})
            p.unlink()
            try:
                call_data_snapshot(reg, {"data_handle": r["data_handle"]})
                t("rejected", False, detail="should have raised")
            except ToolError as e:
                t("rejected", "path is gone" in str(e) or "no such file" in str(e).lower())
        finally:
            if p.exists():
                p.unlink()


# ---------------------------------------------------------------------------
# call_data_unregister
# ---------------------------------------------------------------------------

def test_data_unregister_known_handle():
    print("\n[mcp: data_unregister deletes the handle]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a\n1\n")
        audit = _AuditCollector()
        try:
            r = call_data_register(reg, {"path": str(p)})
            audit.events.clear()
            result = call_data_unregister(
                reg, {"data_handle": r["data_handle"]}, audit=audit,
            )
            t("ok true", result["ok"] is True)
            t("found true", result["found"] is True)
            t("audit emitted",
              len(audit.events) == 1 and audit.events[0][0] == "data.unregistered")
        finally:
            p.unlink()


def test_data_unregister_unknown_idempotent():
    print("\n[mcp: data_unregister on unknown is idempotent]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        result = call_data_unregister(
            reg, {"data_handle": new_handle()},
        )
        t("ok true", result["ok"] is True)
        t("found false", result["found"] is False)


# ---------------------------------------------------------------------------
# Phase 12.8 — snapshot-oversized + policy-violated triggers
# ---------------------------------------------------------------------------

def test_data_register_oversized_snapshot_degrades_to_schema():
    print("\n[mcp: oversized snapshot → schema-only fallback + audit]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        # 60 wide string columns × 50 rows produces a fat snapshot.
        cols = [f"col_{i:02d}" for i in range(60)]
        lines = [",".join(cols)]
        for r in range(50):
            lines.append(",".join([f"row{r}_value_{c}" for c in range(60)]))
        p = _write_csv("\n".join(lines) + "\n")
        audit = _AuditCollector()
        try:
            # Very tight cap forces the gate to trip.
            policy = DataPolicy(snapshot_token_cap=200)
            result = call_data_register(
                reg, {"path": str(p)},
                policy=policy, audit=audit,
            )
            t("result oversized flag set", result.get("oversized") is True)
            t("sample stripped", result["snapshot"]["sample"] == [])
            t("stats stripped", result["snapshot"]["stats"] == {})
            t("schema preserved", len(result["snapshot"]["schema"]) == 60)
            t("file metadata preserved", "file" in result["snapshot"])
            t("truncated marker on payload",
              result["snapshot"].get("truncated") is True)
            kinds = [e[0] for e in audit.events]
            t("data.snapshot_oversized emitted",
              "data.snapshot_oversized" in kinds)
            oversized = [e for e in audit.events
                         if e[0] == "data.snapshot_oversized"][0][1]
            t("audit carries cap_tokens", oversized["cap_tokens"] == 200)
            t("audit carries estimated_tokens > cap",
              oversized["estimated_tokens"] > 200)
            t("audit carries columns count",
              oversized["columns"] == 60)
            t("audit has no sample/values",
              "sample" not in oversized and "stats" not in oversized)
        finally:
            p.unlink()


def test_data_register_under_cap_payload_intact():
    print("\n[mcp: snapshot under cap → full payload, no oversized event]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        p = _write_csv("a,b\n1,2\n3,4\n")
        audit = _AuditCollector()
        try:
            policy = DataPolicy(snapshot_token_cap=4_000)
            result = call_data_register(
                reg, {"path": str(p)}, policy=policy, audit=audit,
            )
            t("result oversized flag false", result.get("oversized") is False)
            t("sample present", len(result["snapshot"]["sample"]) > 0)
            kinds = [e[0] for e in audit.events]
            t("no snapshot_oversized event",
              "data.snapshot_oversized" not in kinds)
        finally:
            p.unlink()


def test_data_register_cap_zero_disables_gate():
    print("\n[mcp: snapshot_token_cap=0 disables the gate (opt-out)]")
    # We can't use DataPolicy(snapshot_token_cap=0) because the dataclass
    # validates >= 100. So we test the helper directly.
    from forge.corvin_data.mcp_handlers import _apply_token_cap  # type: ignore
    huge = {"file": {}, "schema": [{"x": "y"} for _ in range(1000)],
            "sample": [], "stats": {}}
    audit = _AuditCollector()
    payload, oversized = _apply_token_cap(
        huge, cap_tokens=0, audit=audit, handle="data_test",
    )
    t("payload returned unchanged", payload is huge)
    t("oversized flag false", oversized is False)
    t("no audit emitted on opt-out", audit.events == [])


def test_data_register_strict_mode_unsupported_format_emits_policy_violated():
    print("\n[mcp: strict_mode + unsupported format → policy_violated + ToolError]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        # Make a binary file that sniff_format will reject.
        bad = Path(td) / "blob.unknown"
        bad.write_bytes(b"\x00\x01\x02\x03binary-stuff\xff\xfe")
        audit = _AuditCollector()
        try:
            policy = DataPolicy(strict_mode=True)
            call_data_register(
                reg, {"path": str(bad)},
                policy=policy, audit=audit,
            )
            t("expected ToolError but got success", False)
        except ToolError as exc:
            t("ToolError raised", "unsupported" in exc.message.lower())
            kinds = [e[0] for e in audit.events]
            t("data.policy_violated emitted",
              "data.policy_violated" in kinds)
            violated = [e for e in audit.events
                        if e[0] == "data.policy_violated"][0][1]
            t("reason field present", violated.get("reason") == "unsupported-format")
            t("path_hint present (basename only)",
              violated.get("path_hint") == "blob.unknown")
            t("no audit carries full path",
              not any(str(bad) in str(v) for v in violated.values()))


def test_data_register_permissive_mode_no_policy_violated():
    print("\n[mcp: strict_mode=False + unsupported format → no policy_violated]")
    with tempfile.TemporaryDirectory() as td:
        reg = DataRegistry(Path(td))
        bad = Path(td) / "blob.unknown"
        bad.write_bytes(b"\x00\x01binary\xff\xfe")
        audit = _AuditCollector()
        try:
            call_data_register(
                reg, {"path": str(bad)},
                policy=DataPolicy(strict_mode=False),
                audit=audit,
            )
            t("expected ToolError but got success", False)
        except ToolError:
            kinds = [e[0] for e in audit.events]
            t("no policy_violated in permissive mode",
              "data.policy_violated" not in kinds)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    test_new_handle_shape()
    test_is_handle_shape_rejects_bad()
    test_compute_file_hash_deterministic()
    test_compute_file_hash_changes()

    test_registry_register_returns_record()
    test_registry_get_roundtrip()
    test_registry_get_unknown_raises()
    test_registry_malformed_handle_rejected()
    test_registry_delete_idempotent()
    test_registry_list_returns_all()

    test_data_register_full_pipeline()
    test_data_register_audit_carries_no_values()
    test_data_register_missing_path_raises_toolerror()
    test_data_register_nonexistent_path()
    test_data_register_with_explicit_format()
    test_data_register_snapshot_options_passthrough()

    test_data_snapshot_after_register()
    test_data_snapshot_unknown_handle()
    test_data_snapshot_malformed_handle()
    test_data_snapshot_when_file_moved()

    test_data_unregister_known_handle()
    test_data_unregister_unknown_idempotent()

    # Phase 12.8 — oversized + policy_violated triggers
    test_data_register_oversized_snapshot_degrades_to_schema()
    test_data_register_under_cap_payload_intact()
    test_data_register_cap_zero_disables_gate()
    test_data_register_strict_mode_unsupported_format_emits_policy_violated()
    test_data_register_permissive_mode_no_policy_violated()

    print(f"\n{'=' * 50}")
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
