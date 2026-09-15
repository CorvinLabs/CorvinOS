"""E2E for personal_tools → adapter wiring (Layer 27).

Verifies the discovery block is appended to the system prompt by
``_resolve_spawn_inputs`` whenever the user's me.* registry has at
least one entry.
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
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-pt-"))
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)

        # Force re-import so personal_tools picks up the new CORVIN_HOME.
        for mod in ("personal_tools", "user_style", "adapter"):
            sys.modules.pop(mod, None)
        import personal_tools as pt  # noqa: E402
        import adapter as ad         # noqa: E402
        self.pt = pt
        self.ad = ad

    def tearDown(self) -> None:
        for k in ("CORVIN_HOME", "CORVIN_HOME",
                  "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class WiringTests(_AdapterBase):
    def _resolve(self) -> dict:
        return self.ad._resolve_spawn_inputs(
            "hello", "unrestricted", profile=None, add_dir=None,
            channel="discord", chat_key="chat_X", msg_id="m_1",
        )

    def test_no_block_when_no_personal_tools(self) -> None:
        out = self._resolve()
        self.assertNotIn(self.pt.INJECT_HEADING, out["system"])

    def test_block_appears_when_personal_tool_saved(self) -> None:
        self.pt.save_from_body(
            "poke_api",
            description="hits my private API",
            impl_text="def run(url):\n    return {'ok': True}\n",
            corvin_home=self.tmp,
        )
        out = self._resolve()
        self.assertIn(self.pt.INJECT_HEADING, out["system"])
        self.assertIn("`me.poke_api`", out["system"])
        self.assertIn("hits my private API", out["system"])

    def test_block_lists_multiple_tools(self) -> None:
        self.pt.save_from_body(
            "alpha", description="alpha tool",
            impl_text="def run(): return {}\n",
            corvin_home=self.tmp,
        )
        self.pt.save_from_body(
            "beta", description="beta tool",
            impl_text="def run(): return {}\n",
            corvin_home=self.tmp,
        )
        out = self._resolve()
        self.assertIn("`me.alpha`", out["system"])
        self.assertIn("`me.beta`", out["system"])

    def test_module_unavailable_is_silent(self) -> None:
        self.pt.save_from_body(
            "x", description="d",
            impl_text="def run(): return {}\n",
            corvin_home=self.tmp,
        )
        original = self.ad._personal_tools
        self.ad._personal_tools = None
        try:
            out = self._resolve()
            self.assertNotIn(self.pt.INJECT_HEADING, out["system"])
        finally:
            self.ad._personal_tools = original

    def test_block_appears_after_user_style_block(self) -> None:
        """user_style and personal_tools are both injected; verify
        order is user_style first, personal_tools second so the LLM
        scans through the more-stable knowledge before reaching the
        tool list."""
        # Seed user_style live bullet
        import user_style as us
        live = us.Candidate(
            bullet_id="bl", cluster_id="cl", skill_name="skill_x",
            bullet_text="STYLE-LIVE bullet",
            state="live", live_started_at=1.0,
        )
        us.save_live([live], corvin_home=self.tmp)

        # Seed personal tool
        self.pt.save_from_body(
            "tool_x", description="tool x",
            impl_text="def run(): return {}\n",
            corvin_home=self.tmp,
        )
        out = self._resolve()
        sysp = out["system"]
        self.assertIn("STYLE-LIVE bullet", sysp)
        self.assertIn("`me.tool_x`", sysp)
        self.assertLess(
            sysp.index("STYLE-LIVE"), sysp.index("`me.tool_x`"),
            "user_style block must appear BEFORE personal_tools block",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
