"""L25 Compute Worker → background-completion notification wiring.

Proves a detached compute run notifies the originating messenger on completion:
  submit_run(notify={discord origin}) → run executes → mark_done →
  completion_notify.deliver_ready writes a routed outbox envelope.

Plus the client's env-origin auto-injection (CORVIN_CHANNEL_ID) and opt-out.

Run: python3 core/compute/tests/test_compute_notify.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
_SHARED = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
sys.path.insert(0, str(_SHARED))
# The sibling harness ``test_worker`` is imported top-level. Under a direct
# ``python core/compute/tests/test_compute_notify.py`` run the script's own dir
# is sys.path[0] so it resolves — but under ``pytest core/compute/tests`` this
# module is collected as a package member (tests/__init__.py exists) and the
# tests dir is NOT auto-added, so the import fails and takes down the WHOLE
# compute suite's collection. Insert the tests dir explicitly so both work.
_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))

from corvin_compute.client import _origin_from_env  # noqa: E402
from test_worker import _WorkerHarness, _identity_runner  # noqa: E402


class ClientOriginInjectionTests(unittest.TestCase):
    def test_messenger_origin_derived(self):
        os.environ["CORVIN_CHANNEL_ID"] = "discord:1501540900529246251"
        try:
            o = _origin_from_env()
            self.assertEqual(o["channel"], "discord")
            # 19-digit snowflake preserved exactly as a string.
            self.assertEqual(o["chat_id"], "1501540900529246251")
        finally:
            os.environ.pop("CORVIN_CHANNEL_ID", None)

    def test_web_and_unset_yield_none(self):
        os.environ["CORVIN_CHANNEL_ID"] = "web:sid123"
        self.assertIsNone(_origin_from_env())
        os.environ.pop("CORVIN_CHANNEL_ID", None)
        self.assertIsNone(_origin_from_env())

    def test_sender_uses_origin_sender_uid_for_erasure(self):
        # GDPR Art. 17: the record's sender MUST be the real uid (set by the
        # spawn env), not the chat_id — else purge_user(uid) can't match it.
        os.environ["CORVIN_CHANNEL_ID"] = "discord:555000111"
        os.environ["CORVIN_ORIGIN_SENDER"] = "user_uid_42"
        try:
            o = _origin_from_env()
            self.assertEqual(o["sender"], "user_uid_42")
            self.assertNotEqual(o["sender"], o["chat_id"])
        finally:
            os.environ.pop("CORVIN_CHANNEL_ID", None)
            os.environ.pop("CORVIN_ORIGIN_SENDER", None)


class ComputeCompletionNotifyTests(unittest.TestCase):
    def test_run_with_notify_delivers_completion(self):
        h = _WorkerHarness(runner=_identity_runner())
        # completion_notify + the worker resolve the queue via CORVIN_HOME; pin
        # it to the harness home so the record lands where we can read it.
        os.environ["CORVIN_HOME"] = str(h.corvin_home)
        outbox = h.corvin_home / "outbox"
        os.environ.pop("CORVIN_CHANNEL_ID", None)  # force explicit notify only
        client = h.start()
        try:
            import completion_notify as cn  # noqa: PLC0415
            # reload so it picks up the pinned CORVIN_HOME
            import importlib
            importlib.reload(cn)

            sub = client.submit_run(
                tenant_id="_default", tool_name="echo",
                param_grid={"x": [0.1, 0.2, 0.3]}, loss_metric="loss",
                strategy="grid", budget={"max_iterations": 10, "max_wall_clock_s": 5},
                notify={"channel": "discord", "chat_id": "555000111",
                        "sender": "u42"},
            )
            handle = sub["compute_handle"]
            # a record was registered at submit
            self.assertIsNotNone(cn._read(cn._record_path(handle)))

            # poll to terminal
            for _ in range(60):
                st = client.get_status(handle)
                if st["state"] in ("converged", "stalled", "budget_exhausted",
                                    "failed", "aborted"):
                    break
                time.sleep(0.1)
            client.get_result(handle, wait_s=5.0)
            time.sleep(0.2)  # let the completion hook mark_done

            n = cn.deliver_ready(outbox)
            self.assertEqual(n, 1, f"expected 1 delivered, got {n}")
            env = json.loads(next(outbox.glob("cn_*.json")).read_text())
            self.assertEqual(env["channel"], "discord")
            self.assertEqual(env["chat_id"], "555000111")  # string, snowflake-safe
            self.assertIn("Compute run", env["text"])
            self.assertIn("finished", env["text"])
            self.assertTrue(env.get("provenance", {}).get("ai_generated"))
        finally:
            h.stop()
            os.environ.pop("CORVIN_HOME", None)

    def test_notify_with_empty_sender_is_not_registered(self):
        # PENTEST-8: an empty sender would leave an un-erasable record
        # (purge_user matches on sender). Such a notify must be skipped.
        h = _WorkerHarness(runner=_identity_runner())
        os.environ["CORVIN_HOME"] = str(h.corvin_home)
        os.environ.pop("CORVIN_CHANNEL_ID", None)
        client = h.start()
        try:
            import completion_notify as cn  # noqa: PLC0415
            import importlib
            importlib.reload(cn)
            sub = client.submit_run(
                tenant_id="_default", tool_name="echo",
                param_grid={"x": [0.1]}, loss_metric="loss",
                strategy="grid", budget={"max_iterations": 5, "max_wall_clock_s": 5},
                notify={"channel": "discord", "chat_id": "555000111",
                        "sender": ""},  # empty sender → must be skipped
            )
            handle = sub["compute_handle"]
            # No record registered despite a notify block, because sender empty.
            self.assertIsNone(cn._read(cn._record_path(handle)))
            for _ in range(60):
                if client.get_status(handle)["state"] in (
                        "converged", "stalled", "budget_exhausted", "failed", "aborted"):
                    break
                time.sleep(0.1)
            time.sleep(0.2)
            self.assertEqual(cn.deliver_ready(h.corvin_home / "outbox"), 0)
        finally:
            h.stop()
            os.environ.pop("CORVIN_HOME", None)

    def test_run_without_notify_is_poll_only(self):
        h = _WorkerHarness(runner=_identity_runner())
        os.environ["CORVIN_HOME"] = str(h.corvin_home)
        os.environ.pop("CORVIN_CHANNEL_ID", None)
        client = h.start()
        try:
            import completion_notify as cn  # noqa: PLC0415
            import importlib
            importlib.reload(cn)
            sub = client.submit_run(
                tenant_id="_default", tool_name="echo",
                param_grid={"x": [0.1]}, loss_metric="loss",
                strategy="grid", budget={"max_iterations": 5, "max_wall_clock_s": 5},
            )
            handle = sub["compute_handle"]
            for _ in range(60):
                if client.get_status(handle)["state"] in (
                        "converged", "stalled", "budget_exhausted", "failed", "aborted"):
                    break
                time.sleep(0.1)
            time.sleep(0.2)
            # No record registered → nothing to deliver.
            self.assertIsNone(cn._read(cn._record_path(handle)))
            self.assertEqual(cn.deliver_ready(h.corvin_home / "outbox"), 0)
        finally:
            h.stop()
            os.environ.pop("CORVIN_HOME", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
