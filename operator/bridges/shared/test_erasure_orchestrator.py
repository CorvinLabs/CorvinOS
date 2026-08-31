"""Unit tests for erasure_orchestrator.py (Layer 36).

Run with::

    python3 operator/bridges/shared/test_erasure_orchestrator.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from erasure_orchestrator import (  # noqa: E402
    _AUDIT_ALLOWED,
    _REASON_CODES,
    _aggregate_status,
    _assert_safe_audit_value,
    _derive_reason_code,
    _validate_audit_details,
    builtin_stub_chain,
    ErasureLayerResult,
    ErasureOrchestrator,
    ErasureRequest,
    ErasureScopeError,
    LayerStatus,
    OverallStatus,
    ReasonCode,
    StubHandler,
    validate_subject_id,
)


# ----- fake handlers used across multiple test classes ----------------

@dataclass
class _CountingHandler:
    layer_id: str
    count_to_report: int = 1
    status: LayerStatus = LayerStatus.APPLIED

    def purge(self, subject_id, request_id):
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=self.status,
            count=self.count_to_report,
            reason=f"counted {self.count_to_report}",
        )


@dataclass
class _RaisingHandler:
    layer_id: str
    message: str = "boom"

    def purge(self, subject_id, request_id):
        raise RuntimeError(self.message)


@dataclass
class _BadReturnHandler:
    """Handler that returns the wrong type — orchestrator must coerce."""
    layer_id: str

    def purge(self, subject_id, request_id):
        return "not an ErasureLayerResult"


@dataclass
class _MisAttributedHandler:
    """Handler whose result claims a different layer_id — orchestrator
    must correct it."""
    layer_id: str

    def purge(self, subject_id, request_id):
        return ErasureLayerResult(
            layer_id="i-am-someone-else",
            status=LayerStatus.APPLIED,
            count=1,
        )


# ----- tests ----------------------------------------------------------

class TestValidateSubjectId(unittest.TestCase):

    def test_accepts_alnum_dash_underscore_dot(self):
        for s in ["user_42", "User-42", "user.42", "u42", "X"]:
            self.assertEqual(validate_subject_id(s), s)

    def test_accepts_colon_for_bridge_chat_key_style(self):
        # Colon admitted so "discord:12345" works as a subject_id without
        # a separate identity-mapping step.  The @ sign is still rejected.
        for s in ["discord:12345", "telegram:67890", "whatsapp:491234567890"]:
            self.assertEqual(validate_subject_id(s), s)

    def test_rejects_pii_shape(self):
        with self.assertRaises(ValueError):
            validate_subject_id("alice@example.com")
        with self.assertRaises(ValueError):
            validate_subject_id("Alice Smith")
        with self.assertRaises(ValueError):
            validate_subject_id("../etc/passwd")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_subject_id("")

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            validate_subject_id(42)  # type: ignore[arg-type]

    def test_length_capped(self):
        with self.assertRaises(ValueError):
            validate_subject_id("a" * 129)


class TestErasureRequest(unittest.TestCase):

    def test_auto_request_id(self):
        r1 = ErasureRequest(subject_id="u1", requester="dpo")
        r2 = ErasureRequest(subject_id="u1", requester="dpo")
        self.assertNotEqual(r1.request_id, r2.request_id)
        self.assertTrue(r1.request_id.startswith("er-"))

    def test_validates_subject(self):
        with self.assertRaises(ValueError):
            ErasureRequest(subject_id="alice@example.com", requester="dpo")

    def test_requires_requester(self):
        with self.assertRaises(ValueError):
            ErasureRequest(subject_id="u1", requester="")
        with self.assertRaises(ValueError):
            ErasureRequest(subject_id="u1", requester="  ")


class TestOrchestratorHappyPath(unittest.TestCase):

    def setUp(self):
        self.events = []

        def writer(et, sev, det):
            self.events.append((et, sev, dict(det)))

        self.tmpdir = tempfile.TemporaryDirectory()
        self.orch = ErasureOrchestrator(
            tenant_id="_default",
            trail_dir=Path(self.tmpdir.name) / "erasure",
            audit_writer=writer,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_two_handlers_applied(self):
        self.orch.register_handler(_CountingHandler("L28-recall", 5))
        self.orch.register_handler(_CountingHandler("L33-artifacts", 2))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)

        self.assertEqual(result.overall_status, OverallStatus.COMPLETED)
        self.assertEqual(len(result.per_layer), 2)
        self.assertEqual(result.applied_count, 2)
        self.assertEqual(result.failed_count, 0)
        layers = [r.layer_id for r in result.per_layer]
        self.assertEqual(layers, ["L28-recall", "L33-artifacts"])

    def test_audit_emission_order(self):
        self.orch.register_handler(_CountingHandler("L28-recall"))
        self.orch.register_handler(_CountingHandler("L33-artifacts"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        self.orch.execute(req)

        event_types = [e[0] for e in self.events]
        self.assertEqual(event_types[0], "erasure.requested")
        self.assertEqual(event_types[-1], "erasure.completed")
        self.assertEqual(event_types[1:-1], ["erasure.applied", "erasure.applied"])

    def test_audit_severities(self):
        self.orch.register_handler(_CountingHandler("L28-recall"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        self.orch.execute(req)

        # requested + completed are WARNING; applied is INFO
        self.assertEqual(self.events[0][1], "WARNING")  # requested
        self.assertEqual(self.events[1][1], "INFO")     # applied
        self.assertEqual(self.events[-1][1], "WARNING")  # completed

    def test_trail_file_persisted(self):
        self.orch.register_handler(_CountingHandler("L28-recall", 3))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)
        trail = Path(self.tmpdir.name) / "erasure" / f"{req.request_id}.json"
        self.assertTrue(trail.is_file())
        # mode 0600
        mode = trail.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        data = json.loads(trail.read_text())
        self.assertEqual(data["request"]["subject_id"], "user_42")
        self.assertEqual(data["overall_status"], "completed")


class TestOrchestratorFailurePaths(unittest.TestCase):

    def setUp(self):
        self.events = []

        def writer(et, sev, det):
            self.events.append((et, sev, dict(det)))

        self.tmpdir = tempfile.TemporaryDirectory()
        self.orch = ErasureOrchestrator(
            tenant_id="_default",
            trail_dir=Path(self.tmpdir.name) / "erasure",
            audit_writer=writer,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_raising_handler_caught(self):
        self.orch.register_handler(_CountingHandler("L28-recall"))
        self.orch.register_handler(_RaisingHandler("L33-artifacts", "db down"))
        self.orch.register_handler(_CountingHandler("L7-skill-forge"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)

        # Overall is PARTIAL because one failed but two succeeded.
        self.assertEqual(result.overall_status, OverallStatus.PARTIAL)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.applied_count, 2)
        # Failure event is CRITICAL.
        failed_events = [e for e in self.events if e[0] == "erasure.failed"]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0][1], "CRITICAL")
        # Compliance #3: the raw exception text ("db down") must NOT reach
        # the audit chain — only the controlled reason code does. `reason`
        # is no longer an audit detail key at all.
        det = failed_events[0][2]
        self.assertNotIn("reason", det)
        self.assertEqual(det["code"], "store_error")
        for v in det.values():
            if isinstance(v, str):
                self.assertNotIn("db down", v)
        # The full exception text survives ONLY in the per-layer result
        # (which is what the 0600 trail file records).
        failed_layer = next(r for r in result.per_layer
                            if r.status == LayerStatus.FAILED)
        self.assertIn("db down", failed_layer.reason)

    def test_all_failed_yields_failed(self):
        self.orch.register_handler(_RaisingHandler("L28-recall"))
        self.orch.register_handler(_RaisingHandler("L33-artifacts"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)
        self.assertEqual(result.overall_status, OverallStatus.FAILED)

    def test_no_handlers_registered(self):
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)
        self.assertEqual(result.overall_status, OverallStatus.FAILED)
        self.assertEqual(len(result.per_layer), 0)

    def test_bad_return_coerced_to_failed(self):
        self.orch.register_handler(_BadReturnHandler("L28-recall"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)
        self.assertEqual(result.per_layer[0].status, LayerStatus.FAILED)
        self.assertIn("ErasureLayerResult", result.per_layer[0].reason)

    def test_misattributed_layer_id_corrected(self):
        self.orch.register_handler(_MisAttributedHandler("L28-recall"))
        req = ErasureRequest(subject_id="user_42", requester="dpo")
        result = self.orch.execute(req)
        self.assertEqual(result.per_layer[0].layer_id, "L28-recall")
        self.assertIn("corrected", result.per_layer[0].reason)


class TestSkippedStatus(unittest.TestCase):

    def test_skipped_emits_info(self):
        events = []

        def writer(et, sev, det):
            events.append((et, sev, dict(det)))

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=writer,
            )
            orch.register_handler(_CountingHandler(
                "L24-data-snapshot",
                count_to_report=0,
                status=LayerStatus.SKIPPED,
            ))
            req = ErasureRequest(subject_id="user_42", requester="dpo")
            result = orch.execute(req)
            self.assertEqual(result.overall_status, OverallStatus.COMPLETED)
            self.assertEqual(result.per_layer[0].status, LayerStatus.SKIPPED)
            # Severity INFO for skipped
            skip_events = [e for e in events if e[0] == "erasure.skipped"]
            self.assertEqual(len(skip_events), 1)
            self.assertEqual(skip_events[0][1], "INFO")


class TestDuplicateHandlerRegistration(unittest.TestCase):

    def test_duplicate_layer_id_raises(self):
        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(tenant_id="_default", trail_dir=Path(td), audit_writer=lambda *a, **kw: None)
            orch.register_handler(_CountingHandler("L28-recall"))
            with self.assertRaises(ValueError):
                orch.register_handler(_CountingHandler("L28-recall"))


class TestAuditAllowList(unittest.TestCase):

    def test_keys_match_spec(self):
        # Compliance #3: `reason` (free-form, may carry paths/exceptions) is
        # DELIBERATELY no longer an allowed audit key; the controlled
        # `code` replaces it, plus `error_type` (bare exception class name).
        self.assertEqual(_AUDIT_ALLOWED, frozenset({
            "request_id", "subject_id", "requester", "scope",
            "layer_id", "status", "count", "code", "duration_ms",
            "overall_status", "applied_count", "failed_count", "error_type",
        }))
        self.assertNotIn("reason", _AUDIT_ALLOWED)

    def test_smuggled_key_rejected(self):
        with self.assertRaises(ValueError):
            _validate_audit_details({
                "subject_id": "user_42",
                "user_email": "alice@example.com",  # leaked PII
            })

    def test_audit_emission_only_uses_allow_list(self):
        events = []

        def writer(et, sev, det):
            events.append(det)

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td),
                audit_writer=writer,
            )
            orch.register_handler(_CountingHandler("L28-recall"))
            req = ErasureRequest(
                subject_id="user_42",
                requester="dpo",
                scope="all",
                notes="this should NOT make it into audit",
            )
            orch.execute(req)
            for details in events:
                for k in details:
                    self.assertIn(k, _AUDIT_ALLOWED,
                                  f"smuggled key {k!r} in audit details")


class TestAggregateStatus(unittest.TestCase):

    def test_empty_is_failed(self):
        self.assertEqual(_aggregate_status([]), OverallStatus.FAILED)

    def test_all_applied(self):
        rs = [
            ErasureLayerResult(layer_id="a", status=LayerStatus.APPLIED),
            ErasureLayerResult(layer_id="b", status=LayerStatus.APPLIED),
        ]
        self.assertEqual(_aggregate_status(rs), OverallStatus.COMPLETED)

    def test_applied_plus_skipped_completed(self):
        rs = [
            ErasureLayerResult(layer_id="a", status=LayerStatus.APPLIED),
            ErasureLayerResult(layer_id="b", status=LayerStatus.SKIPPED),
        ]
        self.assertEqual(_aggregate_status(rs), OverallStatus.COMPLETED)

    def test_one_failure_among_successes_is_partial(self):
        rs = [
            ErasureLayerResult(layer_id="a", status=LayerStatus.APPLIED),
            ErasureLayerResult(layer_id="b", status=LayerStatus.FAILED),
        ]
        self.assertEqual(_aggregate_status(rs), OverallStatus.PARTIAL)

    def test_all_failed(self):
        rs = [
            ErasureLayerResult(layer_id="a", status=LayerStatus.FAILED),
            ErasureLayerResult(layer_id="b", status=LayerStatus.FAILED),
        ]
        self.assertEqual(_aggregate_status(rs), OverallStatus.FAILED)


class TestStubHandlerAndBuiltinChain(unittest.TestCase):

    def test_stub_returns_skipped(self):
        h = StubHandler("L99-test")
        r = h.purge("user_1", "er-123")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertEqual(r.layer_id, "L99-test")

    def test_builtin_chain_covers_expected_layers(self):
        chain = builtin_stub_chain()
        layer_ids = [h.layer_id for h in chain]
        self.assertIn("L28-recall", layer_ids)
        self.assertIn("L33-artifacts", layer_ids)
        self.assertIn("L7-skill-forge", layer_ids)
        self.assertIn("L24-data-snapshot", layer_ids)
        self.assertIn("L16-identity-mapping", layer_ids)


class TestNoAnthropicImport(unittest.TestCase):

    def test_no_anthropic_in_source(self):
        import ast
        src = (Path(__file__).resolve().parent / "erasure_orchestrator.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


class TestL28UserModelHandler(unittest.TestCase):
    """V-001: L28UserModelHandler purges user-model JSON files."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        import os
        os.environ["CORVIN_HOME"] = str(self.tmp)
        # Build the user_model dir
        self.udir = self.tmp / "tenants" / "_default" / "global" / "memory" / "user_model"
        self.udir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_model_file(self, channel: str, chat_key: str) -> Path:
        """Write a minimal user-model JSON for (channel, chat_key)."""
        import re
        _SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
        safe_ch = _SAFE_RE.sub("_", channel)[:128] or "_"
        safe_ck = _SAFE_RE.sub("_", chat_key)[:128] or "_"
        fname = f"{safe_ch}__{safe_ck}.json"
        path = self.udir / fname
        path.write_text('{"apiVersion":"corvin/v1","kind":"UserModel",'
                        '"metadata":{"channel":"' + channel + '","chat_key":"' + chat_key + '",'
                        '"created_at":0.0,"updated_at":0.0,"distill_count":0},'
                        '"spec":{"communication_style":"","preferences":[],'
                        '"recurring_topics":[],"goals":[],"patterns":[],"do_not_assume":[]}}')
        return path

    def test_erasure_user_model_handler_applied(self):
        """Handler deletes the user-model file and returns APPLIED."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from erasure_handlers import L28UserModelHandler

        chat_key = "1234567890"
        path = self._write_model_file("discord", chat_key)
        self.assertTrue(path.exists())

        handler = L28UserModelHandler(tenant_id="_default")
        result = handler.purge(subject_id=chat_key, request_id="er-test001")

        self.assertEqual(result.status, LayerStatus.APPLIED)
        self.assertEqual(result.count, 1)
        self.assertFalse(path.exists(), "user-model file should be deleted")

    def test_erasure_user_model_handler_skipped_when_absent(self):
        """Handler returns SKIPPED when no matching files exist."""
        from erasure_handlers import L28UserModelHandler

        handler = L28UserModelHandler(tenant_id="_default")
        result = handler.purge(subject_id="nobody", request_id="er-test002")

        self.assertEqual(result.status, LayerStatus.SKIPPED)
        self.assertEqual(result.count, 0)

    def test_erasure_user_model_handler_in_real_chain(self):
        """real_handler_chain() includes L28.2-user-model after L28-recall."""
        from erasure_handlers import real_handler_chain

        chain = real_handler_chain()
        ids = [h.layer_id for h in chain]
        self.assertIn("L28.2-user-model", ids)
        # Must appear right after L28-recall
        recall_idx = ids.index("L28-recall")
        model_idx = ids.index("L28.2-user-model")
        self.assertEqual(model_idx, recall_idx + 1)

    def test_erasure_user_model_handler_deletes_file(self) -> None:
        """V-001 / ADR-0072: L28UserModelHandler.purge() deletes the
        user-model file for the given subject_id and returns APPLIED."""
        import os as _os
        from erasure_handlers import L28UserModelHandler

        # Place the file at the canonical path for subject "discord__12345".
        # The handler sanitises subject_id the same way _write_model_file does
        # (non-alnum chars → '_'), so "discord__12345" → "discord__12345".
        channel = "discord"
        chat_key = "12345"
        pii_content = json.dumps({
            "name": "Alice Smith",
            "email": "alice@example.com",
            "preferences": ["verbose", "formal"],
        })
        path = self._write_model_file(channel, chat_key)
        path.write_text(pii_content)  # overwrite with PII-like content
        self.assertTrue(path.exists(), "precondition: file must exist before purge")

        _os.environ["CORVIN_HOME"] = str(self.tmp)
        handler = L28UserModelHandler(tenant_id="_default")
        result = handler.purge(subject_id=chat_key, request_id="er-del-001")

        self.assertFalse(path.exists(),
                         "user-model file must be deleted after purge")
        self.assertEqual(result.status, LayerStatus.APPLIED,
                         f"expected APPLIED, got {result.status}")
        self.assertGreaterEqual(result.count, 1,
                                "count must be >= 1 for a deleted file")


class TestErasureScopeError(unittest.TestCase):
    """Structural enforcement: cross-tenant erasure must raise ErasureScopeError."""

    def test_mismatched_tenant_id_raises_before_any_handler(self):
        """A request carrying a different tenant_id is rejected immediately."""
        events = []

        def writer(et, sev, det):
            events.append(et)

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=writer,
            )
            orch.register_handler(_CountingHandler("L28-recall"))
            req = ErasureRequest(
                subject_id="user_42",
                requester="dpo",
                tenant_id="foreign_tenant",
            )
            with self.assertRaises(ErasureScopeError):
                orch.execute(req)
        # No audit events should have been emitted (guard fires before audit write)
        self.assertEqual(events, [],
                         "audit events must not be emitted for cross-tenant attempt")

    def test_matching_tenant_id_executes_normally(self):
        """A request with a matching tenant_id runs without raising."""
        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=lambda *a, **kw: None,
            )
            orch.register_handler(_CountingHandler("L28-recall"))
            req = ErasureRequest(
                subject_id="user_42",
                requester="dpo",
                tenant_id="_default",
            )
            result = orch.execute(req)
        self.assertEqual(result.overall_status, OverallStatus.COMPLETED)

    def test_no_tenant_id_on_request_passes_guard(self):
        """Requests without tenant_id (legacy callers) are not blocked."""
        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=lambda *a, **kw: None,
            )
            orch.register_handler(_CountingHandler("L28-recall"))
            req = ErasureRequest(subject_id="user_42", requester="dpo")
            # tenant_id defaults to None — guard must not fire
            result = orch.execute(req)
        self.assertEqual(result.overall_status, OverallStatus.COMPLETED)


@dataclass
class _PathLeakingHandler:
    """Handler that stuffs an ABSOLUTE PATH and raw exception text into
    its result `reason` — the exact leak vector compliance issue #3
    closes. The orchestrator must keep this text OUT of the audit chain."""
    layer_id: str
    status: LayerStatus = LayerStatus.SKIPPED

    def purge(self, subject_id, request_id):
        # Note: NO `code` set — exercises the orchestrator's safe derivation.
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=self.status,
            count=0,
            reason="recall.db not present at /home/alice/.corvin/tenants/"
                   "_default/global/memory/recall.db; OSError: [Errno 13] "
                   "Permission denied: '/secret/path'",
        )


class TestReasonCodeControlledVocabulary(unittest.TestCase):
    """Compliance #3: absolute paths + raw exception text in a handler's
    free-form `reason` must NEVER reach the L16 audit chain. Only a
    controlled reason CODE is emitted; the descriptive text is confined to
    the 0600 trail file."""

    def _run(self, handler):
        events = []

        def writer(et, sev, det):
            events.append((et, sev, dict(det)))

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=writer,
            )
            orch.register_handler(handler)
            req = ErasureRequest(subject_id="user_42", requester="dpo")
            result = orch.execute(req)
            trail = Path(td) / "erasure" / f"{req.request_id}.json"
            trail_data = json.loads(trail.read_text())
        return events, result, trail_data

    def test_forced_path_and_exception_reason_not_in_audit(self):
        events, result, trail_data = self._run(
            _PathLeakingHandler("L28-recall", LayerStatus.SKIPPED))

        # (a) NO audit detail value may contain an absolute path or raw
        #     exception text — on ANY of requested/applied/skipped/failed.
        for et, _sev, det in events:
            self.assertNotIn("reason", det,
                             f"`reason` leaked into audit event {et!r}")
            for k, v in det.items():
                if isinstance(v, str):
                    self.assertNotIn("/home/alice", v,
                                     f"abs path leaked via key {k!r} in {et!r}")
                    self.assertNotIn("recall.db", v,
                                     f"path fragment leaked via {k!r} in {et!r}")
                    self.assertNotIn("Permission denied", v,
                                     f"exception text leaked via {k!r} in {et!r}")
                    self.assertNotIn("Errno", v,
                                     f"errno leaked via key {k!r} in {et!r}")

        # The per-layer audit event carries ONLY a controlled code.
        skip = [e for e in events if e[0] == "erasure.skipped"]
        self.assertEqual(len(skip), 1)
        self.assertIn(skip[0][2]["code"], _REASON_CODES)
        self.assertEqual(skip[0][2]["code"], "store_empty")  # derived from SKIPPED

        # (b) The full descriptive path/exception text DOES survive in the
        #     0600 trail file (which L36 permits to hold free-form notes).
        layer = trail_data["per_layer"][0]
        self.assertIn("/home/alice", layer["reason"])
        self.assertIn("Permission denied", layer["reason"])
        self.assertEqual(layer["code"], "store_empty")

    def test_failed_handler_emits_store_error_code_only(self):
        events, result, trail_data = self._run(
            _RaisingHandler("L33-artifacts",
                            "OSError: /var/lib/corvin/x.db is locked"))
        failed = [e for e in events if e[0] == "erasure.failed"]
        self.assertEqual(len(failed), 1)
        det = failed[0][2]
        self.assertNotIn("reason", det)
        self.assertEqual(det["code"], "store_error")
        for v in det.values():
            if isinstance(v, str):
                self.assertNotIn("/var/lib", v)
        # trail keeps the exception text
        layer = trail_data["per_layer"][0]
        self.assertIn("is locked", layer["reason"])

    def test_all_handler_reason_codes_are_controlled(self):
        self.assertEqual(
            _REASON_CODES,
            frozenset({"deleted", "store_absent", "store_empty",
                       "not_applicable", "store_error",
                       "handler_contract_error"}),
        )

    def test_derive_reason_code_uses_explicit_when_valid(self):
        self.assertEqual(
            _derive_reason_code(LayerStatus.SKIPPED, "store_absent"),
            "store_absent")

    def test_derive_reason_code_rejects_free_text_falls_back_to_status(self):
        # A handler smuggling a path into `code` cannot leak it — the
        # derivation discards anything not in the closed vocabulary.
        leaked = "/home/alice/.corvin/recall.db"
        self.assertEqual(
            _derive_reason_code(LayerStatus.APPLIED, leaked), "deleted")
        self.assertEqual(
            _derive_reason_code(LayerStatus.FAILED, leaked), "store_error")


class TestAuditValueScrubber(unittest.TestCase):
    """Fail-closed defence-in-depth: the value scrubber refuses any audit
    detail value carrying a path separator or exception-shaped text, and
    enforces the controlled vocabulary on `code`."""

    def test_code_value_must_be_controlled(self):
        _assert_safe_audit_value("code", "deleted")  # ok
        with self.assertRaises(ValueError):
            _assert_safe_audit_value("code", "made_up_code")
        with self.assertRaises(ValueError):
            _assert_safe_audit_value("code", "/etc/passwd")

    def test_path_separator_rejected_on_unexpected_key(self):
        # A hypothetical future free-form key carrying a path must fail closed.
        with self.assertRaises(ValueError):
            _validate_audit_details({"request_id": "er-1",
                                     "layer_id": "/abs/path/leak"})

    def test_exception_text_rejected_on_unexpected_key(self):
        with self.assertRaises(ValueError):
            _validate_audit_details({"request_id": "er-1",
                                     "status": "Traceback (most recent call)"})

    def test_error_type_accepts_class_name(self):
        # "OSError" contains "Error" but is a legitimate class name.
        _assert_safe_audit_value("error_type", "OSError")
        _validate_audit_details({"request_id": "er-1", "error_type": "OSError"})
        with self.assertRaises(ValueError):
            _assert_safe_audit_value("error_type", "no such file: /tmp/x")

    def test_identity_keys_allow_colon_but_no_path(self):
        # subject_id like "discord:123" is fine (colon allowed, no slash).
        _validate_audit_details({"subject_id": "discord:123",
                                 "requester": "dpo@example.com"})

    def test_numeric_values_pass(self):
        _assert_safe_audit_value("count", 5)
        _assert_safe_audit_value("duration_ms", 0)


class TestSubjectIdTrailingNewline(unittest.TestCase):
    """Compliance #4: validate_subject_id must use fullmatch so a trailing
    newline is rejected — a newline-variant could mint a distinct,
    un-erasable pseudonymous identity."""

    def test_trailing_newline_rejected(self):
        with self.assertRaises(ValueError):
            validate_subject_id("subject\n")

    def test_trailing_newline_variants_rejected(self):
        for bad in ["user_42\n", "user_42\r\n", "user_42\r",
                    "\nuser_42", "user_42\n\n", "discord:123\n"]:
            with self.assertRaises(ValueError, msg=f"{bad!r} must be rejected"):
                validate_subject_id(bad)

    def test_clean_value_still_accepted(self):
        self.assertEqual(validate_subject_id("user_42"), "user_42")

    def test_request_rejects_trailing_newline_subject(self):
        with self.assertRaises(ValueError):
            ErasureRequest(subject_id="user_42\n", requester="dpo")


if __name__ == "__main__":
    unittest.main()
