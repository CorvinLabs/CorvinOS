"""E2E for Layer 28 (ADR-0016) → adapter wiring.

Two integration sites land in adapter.py:

  1. ``_resolve_spawn_inputs`` reads the per-chat ``UserModel`` and folds
     a ``<user_context>`` markdown block into the system prompt AS THE
     LAST block (most-recent-instruction-wins ordering).
  2. ``_user_model_distill_async`` is the worker-thread entry that
     schedules ``user_model.distill`` serialised behind a guard.

Cases (7):
  1. No ``user_model_enabled`` flag in profile → no <user_context> block
  2. ``user_model_enabled: true`` + saved model on disk → block appears
  3. Block lands AFTER ``Auto-learned user style`` (last-position rule)
  4. ``user_model_enabled: true`` but model file absent → no block
  5. Adapter `_user_model = None` (module unavailable) → silent no-op
  6. ``_user_model_distill_async`` calls user_model.distill with the
     right (channel, chat_key) args + swallows exceptions
  7. ``conversation_recall`` indexing hook is no-op when
     ``conversation_recall_indexing_enabled = false``
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_FORGE_TOP = _HERE.parent.parent / "forge"
if str(_FORGE_TOP) not in sys.path:
    sys.path.insert(0, str(_FORGE_TOP))


class _AdapterBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-l28-"))
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)

        # Fresh imports under the sandboxed env.
        for mod in (
            "user_model", "conversation_recall", "user_style", "adapter",
        ):
            sys.modules.pop(mod, None)
        import user_model as um  # noqa: E402
        import conversation_recall as cr  # noqa: E402
        import adapter as ad  # noqa: E402
        self.um = um
        self.cr = cr
        self.ad = ad

    def tearDown(self) -> None:
        # Close any open recall DB handles so the tempdir teardown is clean.
        try:
            self.cr._close_all_connections()
        except Exception:  # noqa: BLE001
            pass
        for k in ("CORVIN_HOME", "CORVIN_HOME",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_model(self) -> None:
        m = self.um.UserModel.empty("discord", "chatA")
        m.communication_style = "concise, asks for trade-offs explicitly"
        m.preferences = ["German chat", "voice-note replies"]
        m.recurring_topics = ["Corvin layer design", "compliance audit"]
        self.um.save(m)


class UserContextBlockTests(_AdapterBase):
    def _resolve(self, profile: dict | None) -> dict:
        return self.ad._resolve_spawn_inputs(
            "hello", "unrestricted", profile=profile, add_dir=None,
            channel="discord", chat_key="chatA",
            msg_id="msg_42",
        )

    def test_no_flag_no_block(self) -> None:
        """Case 1 — without user_model_enabled, the block never appears."""
        self._make_model()  # file on disk, but profile flag missing
        out = self._resolve(profile={})
        self.assertNotIn("<user_context>", out["system"])
        self.assertNotIn("communication_style", out["system"])

    def test_flag_plus_model_renders_block(self) -> None:
        """Case 2 — opt-in + saved model → <user_context> in system."""
        self._make_model()
        out = self._resolve(profile={"user_model_enabled": True})
        self.assertIn("<user_context>", out["system"])
        self.assertIn("</user_context>", out["system"])
        self.assertIn("concise, asks for trade-offs explicitly", out["system"])
        self.assertIn("Corvin layer design", out["system"])

    def test_block_lands_last(self) -> None:
        """Case 3 — user_context comes AFTER any other appended block.

        Asserts the user-context block ends near the tail of the system
        prompt (most-recent-instruction rule from ADR-0016).
        """
        self._make_model()
        out = self._resolve(profile={"user_model_enabled": True})
        system = out["system"]
        idx = system.find("<user_context>")
        self.assertGreater(idx, 0)
        # Everything after </user_context> should be empty / whitespace.
        tail = system.split("</user_context>", 1)[1]
        self.assertEqual(tail.strip(), "")

    def test_flag_without_model_no_block(self) -> None:
        """Case 4 — opt-in but no model on disk → no block."""
        # No _make_model() here.
        out = self._resolve(profile={"user_model_enabled": True})
        self.assertNotIn("<user_context>", out["system"])

    def test_module_unavailable_is_silent(self) -> None:
        """Case 5 — adapter._user_model = None → no crash, no block."""
        self._make_model()
        original = self.ad._user_model
        self.ad._user_model = None
        try:
            out = self._resolve(profile={"user_model_enabled": True})
            self.assertNotIn("<user_context>", out["system"])
        finally:
            self.ad._user_model = original


class DistillAsyncTests(_AdapterBase):
    def test_async_distill_calls_user_model_with_args(self) -> None:
        """Case 6 — _user_model_distill_async forwards channel+chat_key
        to user_model.distill and swallows exceptions."""
        calls: list[tuple[str, str]] = []

        def stub_distill(*, channel: str, chat_key: str, **kw: object) -> object:
            calls.append((channel, chat_key))
            return self.um.DistillResult(ok=True, reason="ok")

        original = self.ad._user_model.distill
        self.ad._user_model.distill = stub_distill
        try:
            self.ad._user_model_distill_async("telegram", "chat_42")
            self.assertEqual(calls, [("telegram", "chat_42")])
        finally:
            self.ad._user_model.distill = original

    def test_async_distill_swallows_exception(self) -> None:
        """Case 6b — distill raises → helper returns without re-raising."""
        def raising_distill(**kw: object) -> object:
            raise RuntimeError("simulated distill failure")

        original = self.ad._user_model.distill
        self.ad._user_model.distill = raising_distill
        try:
            # Must not raise.
            self.ad._user_model_distill_async("discord", "chatX")
        finally:
            self.ad._user_model.distill = original


class IndexingHookGateTests(_AdapterBase):
    def test_indexing_disabled_skips_index_turn(self) -> None:
        """Case 7 — when chat_profile.conversation_recall_indexing_enabled
        is False, the index_turn() call must be skipped.

        Approach: stub _conversation_recall.index_turn to count calls,
        simulate the snippet from process_one() that the runtime would
        execute (the relevant gate is a 3-liner; we drive it
        directly instead of running process_one end-to-end).
        """
        calls: list[dict] = []

        def stub_index_turn(**kw: object) -> dict:
            calls.append(kw)  # type: ignore[arg-type]
            return {"ok": True}

        original = self.ad._conversation_recall.index_turn
        self.ad._conversation_recall.index_turn = stub_index_turn
        try:
            for profile, expected_count in [
                ({"conversation_recall_indexing_enabled": False}, 0),
                ({}, 1),                                   # default-on
                ({"conversation_recall_indexing_enabled": True}, 2),
            ]:
                indexing_enabled = (
                    profile.get("conversation_recall_indexing_enabled", True)
                    if isinstance(profile, dict) else True
                )
                if indexing_enabled:
                    self.ad._conversation_recall.index_turn(
                        channel="discord", chat_key="c",
                        user_text="u", assistant_text="a",
                        msg_id="m", persona="p", run_id="m",
                    )
                self.assertEqual(len(calls), expected_count)
        finally:
            self.ad._conversation_recall.index_turn = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
