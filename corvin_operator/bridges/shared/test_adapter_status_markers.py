"""Call-site proof: the status-marker rule reaches the engine on the bridge path.

``system_prompt_for`` carrying the rule proves nothing about a turn: what the
engine sees is the system prompt ``_resolve_spawn_inputs`` composes (base +
profile + memory + chat ``append_system`` + ...), and on the legacy argv path the
file ``_build_claude_args`` writes behind ``--append-system-prompt-file``. These
tests drive those two real builders for a Discord chat (CONCEPT-0114, ADR-2269).
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


class BridgeSpawnCarriesStatusMarkers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-sm-"))
        self._env = {k: os.environ.get(k) for k in
                     ("CORVIN_HOME", "ADAPTER_INBOX", "ADAPTER_OUTBOX")}
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)
        sys.modules.pop("adapter", None)
        import adapter as ad  # noqa: E402
        import status_markers as sm  # noqa: E402
        self.ad = ad
        self.rule = sm.status_marker_rule()

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolved_system_prompt_for_discord_carries_the_rule(self) -> None:
        out = self.ad._resolve_spawn_inputs(
            "hello", "unrestricted", profile=None, add_dir=None,
            channel="discord", chat_key="chat_sm", msg_id="m_sm_1",
        )
        self.assertIn(self.rule, out["system"])
        self.assertIn("Discord", out["system"])

    def test_chat_append_system_does_not_drop_the_rule(self) -> None:
        out = self.ad._resolve_spawn_inputs(
            "hello", "unrestricted",
            profile={"append_system": "CHAT-OVERRIDE-MARKER"}, add_dir=None,
            channel="discord", chat_key="chat_sm", msg_id="m_sm_2",
        )
        system = out["system"]
        self.assertIn(self.rule, system)
        self.assertIn("CHAT-OVERRIDE-MARKER", system)
        # The chat-level override is appended AFTER the base rules, so it wins
        # on conflict — the documented ordering of _resolve_spawn_inputs.
        self.assertLess(system.index(self.rule), system.index("CHAT-OVERRIDE-MARKER"))

    def test_argv_system_prompt_file_carries_the_rule(self) -> None:
        argv = self.ad._build_claude_args(
            "hello", "unrestricted", None, None,
            channel="discord", chat_key="chat_sm", msg_id="m_sm_3",
        )
        self.assertIn("--append-system-prompt-file", argv)
        path = Path(argv[argv.index("--append-system-prompt-file") + 1])
        try:
            self.assertIn(self.rule, path.read_text(encoding="utf-8"))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
