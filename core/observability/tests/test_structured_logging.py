"""Tests for structured logging (ADR-0231 Stage 1).

Three properties carry weight:

* correlation ids must survive async task switches — the design sketch used a
  thread-local, which leaks between concurrent tasks on one thread;
* PII must be redacted rather than raised on, because a logger that raises fails
  the work it was describing;
* no record may carry an exception MESSAGE, only its class.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG = _HERE.parents[1]  # core/observability
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from corvin_logging import (  # noqa: E402
    JsonFormatter,
    contains_pii,
    get_logger,
    new_correlation_id,
    request_context,
    scrub,
    scrub_text,
)
from corvin_logging import context as ctx  # noqa: E402


class TestCorrelationContext(unittest.TestCase):
    def test_absent_by_default(self):
        self.assertIsNone(ctx.get_correlation_id())

    def test_request_context_binds_and_restores(self):
        with request_context("req-outer", tenant_id="t-1") as cid:
            self.assertEqual(cid, "req-outer")
            self.assertEqual(ctx.get_correlation_id(), "req-outer")
            self.assertEqual(ctx.get_tenant_id(), "t-1")
        self.assertIsNone(ctx.get_correlation_id(), "must not leak out of the block")
        self.assertIsNone(ctx.get_tenant_id())

    def test_nesting_restores_the_outer_value(self):
        with request_context("outer"):
            with request_context("inner"):
                self.assertEqual(ctx.get_correlation_id(), "inner")
            self.assertEqual(
                ctx.get_correlation_id(), "outer", "the token API must restore"
            )

    def test_generated_ids_are_unique_and_carry_no_identity(self):
        ids = {new_correlation_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for cid in list(ids)[:5]:
            self.assertTrue(cid.startswith("req-"))

    def test_concurrent_tasks_do_not_share_the_id(self):
        """The thread-local in the design sketch fails exactly here."""
        seen: dict[str, str | None] = {}

        async def worker(name: str) -> None:
            with request_context(f"req-{name}"):
                await asyncio.sleep(0.01)  # force a task switch mid-context
                seen[name] = ctx.get_correlation_id()

        async def main() -> None:
            await asyncio.gather(*(worker(n) for n in ("a", "b", "c")))

        asyncio.run(main())
        self.assertEqual(seen, {"a": "req-a", "b": "req-b", "c": "req-c"})

    def test_threads_do_not_share_the_id(self):
        seen: dict[str, str | None] = {}

        def worker(name: str) -> None:
            with request_context(f"req-{name}"):
                seen[name] = ctx.get_correlation_id()

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("x", "y")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(seen, {"x": "req-x", "y": "req-y"})


class TestScrubber(unittest.TestCase):
    def test_redacts_email(self):
        out, hit = scrub_text("contact jdoe@corp.example now")
        self.assertTrue(hit)
        self.assertNotIn("jdoe", out)
        self.assertIn("[REDACTED:email]", out)

    def test_redacts_connection_string_credentials(self):
        out, hit = scrub_text("postgres://user:s3cret@db.internal/corvin")
        self.assertTrue(hit)
        self.assertNotIn("s3cret", out)

    def test_redacts_tokens_jwt_iban_card_ssn_ip(self):
        cases = {
            "sk-abcdefghijklmnopqrstuvwxyz": "token",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N": "jwt",
            "DE89370400440532013000": "iban",
            "4111 1111 1111 1111": "card",
            "123-45-6789": "ssn",
            "203.0.113.42": "ipv4",
        }
        for payload, kind in cases.items():
            out, hit = scrub_text(f"value={payload}")
            self.assertTrue(hit, f"{kind} not detected in {payload!r}")
            self.assertIn(f"[REDACTED:{kind}]", out, payload)

    def test_loopback_is_not_redacted(self):
        """Redacting 127.0.0.1 would strip useful local-diagnostics noise."""
        out, hit = scrub_text("bound to 127.0.0.1:8765")
        self.assertFalse(hit)
        self.assertIn("127.0.0.1", out)

    def test_sensitive_keys_are_redacted_wholesale(self):
        out, hit = scrub({"password": "anything", "api_key": "x", "keep": "fine"})
        self.assertTrue(hit)
        self.assertEqual(out["password"], "[REDACTED:sensitive-key]")
        self.assertEqual(out["api_key"], "[REDACTED:sensitive-key]")
        self.assertEqual(out["keep"], "fine")

    def test_nested_structures_are_walked(self):
        out, hit = scrub({"a": [{"b": ("mail@x.example",)}]})
        self.assertTrue(hit)
        self.assertIn("REDACTED", out["a"][0]["b"][0])

    def test_deep_structure_is_capped_not_crashed(self):
        deep: dict = {}
        cursor = deep
        for _ in range(20):
            cursor["next"] = {}
            cursor = cursor["next"]
        out, hit = scrub(deep)
        self.assertTrue(hit)
        self.assertIn("too-deep", repr(out))

    def test_non_strings_pass_through(self):
        out, hit = scrub({"n": 42, "f": 1.5, "b": True, "z": None})
        self.assertFalse(hit)
        self.assertEqual(out, {"n": 42, "f": 1.5, "b": True, "z": None})

    def test_scrubber_never_raises(self):
        class Weird:
            def __repr__(self):
                raise RuntimeError("no repr for you")

        out, _hit = scrub({"weird": Weird()})
        self.assertIn("weird", out)

    def test_contains_pii_helper(self):
        self.assertTrue(contains_pii({"x": "a@b.example"}))
        self.assertFalse(contains_pii({"x": "nothing here"}))


class TestCorvinLogger(unittest.TestCase):
    def setUp(self):
        self.log = get_logger("plugins", plugin_id="acme-notify")

    def test_record_carries_the_schema_fields(self):
        with request_context("req-1", tenant_id="t-9"):
            rec = self.log.info(
                "plugin enabled", operation="enable", context={"origin": "vetted"}
            )
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["component"], "plugins")
        self.assertEqual(rec["plugin_id"], "acme-notify")
        self.assertEqual(rec["tenant_id"], "t-9")
        self.assertEqual(rec["correlation_id"], "req-1")
        self.assertEqual(rec["operation"], "enable")
        self.assertEqual(rec["context"], {"origin": "vetted"})

    def test_fields_are_ordered_for_grepping(self):
        rec = self.log.info("x")
        self.assertEqual(list(rec)[:4], ["timestamp", "level", "component", "plugin_id"])

    def test_exception_contributes_class_not_message(self):
        exc = RuntimeError("bind failed for jdoe at /home/secret")
        rec = self.log.error("directory unreachable", error=exc)
        self.assertEqual(rec["error_code"], "RuntimeError")
        blob = repr(rec)
        self.assertNotIn("jdoe", blob)
        self.assertNotIn("/home/secret", blob)

    def test_pii_in_context_is_redacted_and_flagged(self):
        rec = self.log.warn("odd input", context={"who": "a@b.example"})
        self.assertTrue(rec["pii_redacted"])
        self.assertNotIn("a@b.example", repr(rec))

    def test_clean_record_has_no_redaction_flag(self):
        rec = self.log.info("all good", context={"count": 3})
        self.assertNotIn("pii_redacted", rec)

    def test_logging_never_raises(self):
        class Explosive:
            def __getattr__(self, item):
                raise RuntimeError("boom")

        # A context object that misbehaves must not take the caller down.
        rec = self.log.info("weird payload", context={"obj": Explosive()})
        self.assertIsInstance(rec, dict)

    def test_timed_logs_duration_and_reraises(self):
        with self.assertRaises(ValueError):
            with self.log.timed("risky"):
                raise ValueError("nope")

    def test_timed_success_records_duration(self):
        with self.assertLogs("corvin.plugins", level="INFO") as logs:
            with self.log.timed("cheap"):
                pass
        self.assertTrue(any("cheap ok" in line for line in logs.output))

    def test_json_formatter_emits_one_parseable_line(self):
        import json

        record = logging.LogRecord(
            "corvin.plugins", logging.INFO, __file__, 1, "hello", None, None
        )
        record.corvin_event = {"timestamp": "t", "level": "INFO", "message": "hello"}
        line = JsonFormatter().format(record)
        self.assertEqual(json.loads(line)["message"], "hello")
        self.assertNotIn("\n", line, "one record must be one line")

    def test_json_formatter_handles_foreign_records(self):
        import json

        record = logging.LogRecord(
            "some.other.lib", logging.WARNING, __file__, 1, "plain %s", ("msg",), None
        )
        parsed = json.loads(JsonFormatter().format(record))
        self.assertEqual(parsed["message"], "plain msg")
        self.assertEqual(parsed["level"], "WARNING")

    def test_install_json_handler_is_idempotent(self):
        from corvin_logging import install_json_handler

        first = install_json_handler()
        second = install_json_handler()
        self.assertIs(first, second)
        root_handlers = logging.getLogger("corvin").handlers
        self.assertEqual(
            sum(1 for h in root_handlers if getattr(h, "_corvin_json", False)), 1
        )

    def test_package_is_not_named_logging(self):
        """A package named `logging` shadows the stdlib — the operator/ trap."""
        import corvin_logging

        self.assertTrue(corvin_logging.__name__.startswith("corvin_"))
        self.assertNotIn("core/logging", str(Path(corvin_logging.__file__)))


if __name__ == "__main__":
    unittest.main()
