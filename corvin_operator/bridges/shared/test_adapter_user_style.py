"""E2E for user_style → adapter wiring (Layer 26).

Verifies that ``adapter._resolve_spawn_inputs`` correctly fetches the
live + shadow-A/B bullets via ``user_style.shadow_pick_for_turn`` and
splices them into the system prompt with the canonical heading. Drives
the wiring through the public function (no private/internal stubs).

Cases:
  1. No bullets on disk → no "Auto-learned user style" block in system
  2. Live bullet present + shadow empty → live appears regardless of msg_id
  3. Shadow bullet + msg_id with parity 1 → live + shadow appear
  4. Shadow bullet + msg_id with parity 0 → only live appears
  5. Heading + bullet text rendered as Markdown ("- " prefix)
  6. Module gracefully no-ops when import fails (stub via monkey-patch)
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


def _seed_with_parity(target_parity: int) -> str:
    import hashlib
    i = 0
    while True:
        sid = f"msg_{i:08d}"
        if (hashlib.sha256(sid.encode()).digest()[0] & 1) == target_parity:
            return sid
        i += 1
        if i > 100_000:
            raise RuntimeError("could not find seed")


class _AdapterBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-us-"))
        # Re-import adapter under sandboxed env so its module-level
        # constants pick up our temp CORVIN_HOME.
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["CORVIN_HOME"] = str(self.tmp)
        # Adapter imports a lot — keep the import light
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)

        # Force reimport so user_style picks up the new CORVIN_HOME.
        for mod in ("user_style", "adapter"):
            sys.modules.pop(mod, None)
        import user_style as us  # noqa: E402
        import adapter as ad     # noqa: E402
        self.us = us
        self.ad = ad

    def tearDown(self) -> None:
        for k in ("CORVIN_HOME", "CORVIN_HOME",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class WiringTests(_AdapterBase):
    def _resolve(self, msg_id: str | None = None) -> dict:
        return self.ad._resolve_spawn_inputs(
            "hello", "unrestricted", profile=None, add_dir=None,
            channel="discord", chat_key="chat_X",
            msg_id=msg_id,
        )

    # 1. ----------------------------------------------------------------
    def test_no_bullets_no_block(self) -> None:
        out = self._resolve(msg_id="m_42")
        self.assertNotIn("Auto-learned user style", out["system"])

    # 2. ----------------------------------------------------------------
    def test_live_bullet_always_appears(self) -> None:
        live = self.us.Candidate(
            bullet_id="bl_1", cluster_id="cl_1", skill_name="skill_L",
            bullet_text="STYLE-LIVE: prefer 1-line answers",
            state="live", live_started_at=1.0,
        )
        self.us.save_live([live], corvin_home=self.tmp)

        # Try both parities — live always shows.
        for parity in (0, 1):
            seed = _seed_with_parity(parity)
            out = self._resolve(msg_id=seed)
            self.assertIn("Auto-learned user style", out["system"],
                          f"missing block for parity={parity}")
            self.assertIn("STYLE-LIVE: prefer 1-line answers", out["system"])

    # 3. ----------------------------------------------------------------
    def test_shadow_appears_only_for_parity_1(self) -> None:
        shadow = self.us.Candidate(
            bullet_id="bs_1", cluster_id="cl_S", skill_name="skill_S",
            bullet_text="STYLE-SHADOW: trial bullet for A/B",
            state="shadow", shadow_started_at=1.0,
        )
        self.us.save_candidates([shadow], corvin_home=self.tmp)

        seed_with = _seed_with_parity(1)
        out = self._resolve(msg_id=seed_with)
        self.assertIn("STYLE-SHADOW: trial bullet for A/B", out["system"])

        seed_without = _seed_with_parity(0)
        out2 = self._resolve(msg_id=seed_without)
        self.assertNotIn("STYLE-SHADOW: trial bullet for A/B", out2["system"])
        # And the heading also doesn't appear when no live and no shadow
        self.assertNotIn("Auto-learned user style", out2["system"])

    # 4. ----------------------------------------------------------------
    def test_live_plus_shadow_both_appear_on_parity_1(self) -> None:
        live = self.us.Candidate(
            bullet_id="bl", cluster_id="cl_L", skill_name="skill_L",
            bullet_text="STYLE-LIVE: bullet 1",
            state="live", live_started_at=1.0,
        )
        shadow = self.us.Candidate(
            bullet_id="bs", cluster_id="cl_S", skill_name="skill_S",
            bullet_text="STYLE-SHADOW: bullet 2",
            state="shadow", shadow_started_at=1.0,
        )
        self.us.save_live([live], corvin_home=self.tmp)
        self.us.save_candidates([shadow], corvin_home=self.tmp)

        seed = _seed_with_parity(1)
        out = self._resolve(msg_id=seed)
        self.assertIn("- STYLE-LIVE: bullet 1", out["system"])
        self.assertIn("- STYLE-SHADOW: bullet 2", out["system"])
        # Heading only once
        self.assertEqual(out["system"].count("Auto-learned user style"), 1)

    # 5. ----------------------------------------------------------------
    def test_markdown_bullet_format(self) -> None:
        live = self.us.Candidate(
            bullet_id="bl", cluster_id="cl", skill_name="skill_X",
            bullet_text="rule about X",
            state="live", live_started_at=1.0,
        )
        self.us.save_live([live], corvin_home=self.tmp)
        out = self._resolve(msg_id="any")
        # Heading + 1 bullet
        self.assertIn("## Auto-learned user style", out["system"])
        self.assertIn("- rule about X", out["system"])

    # 6. ----------------------------------------------------------------
    def test_module_unavailable_is_silent(self) -> None:
        live = self.us.Candidate(
            bullet_id="bl", cluster_id="cl", skill_name="skill_X",
            bullet_text="rule about X",
            state="live", live_started_at=1.0,
        )
        self.us.save_live([live], corvin_home=self.tmp)

        # Monkey-patch the module reference in adapter to None
        original = self.ad._user_style
        self.ad._user_style = None
        try:
            out = self._resolve(msg_id="any")
            self.assertNotIn("Auto-learned user style", out["system"])
        finally:
            self.ad._user_style = original

    # 7. ----------------------------------------------------------------
    def test_chat_key_fallback_when_msg_id_missing(self) -> None:
        """Without msg_id, chat_key acts as the seed (stable per chat)."""
        shadow = self.us.Candidate(
            bullet_id="bs", cluster_id="cl_S", skill_name="skill_S",
            bullet_text="STYLE-SHADOW: with chat_key seed",
            state="shadow", shadow_started_at=1.0,
        )
        self.us.save_candidates([shadow], corvin_home=self.tmp)

        # Find a chat_key with parity 1
        seed_chat = _seed_with_parity(1)
        out = self.ad._resolve_spawn_inputs(
            "hello", "unrestricted", profile=None, add_dir=None,
            channel="discord", chat_key=seed_chat,
            msg_id=None,
        )
        self.assertIn("STYLE-SHADOW: with chat_key seed", out["system"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
