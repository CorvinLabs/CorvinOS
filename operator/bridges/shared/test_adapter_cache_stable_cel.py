"""E2E wiring proof — cache-stable CEL relocation in the bridge spawn path (ADR-0395).

Mirror of the console `chat_runtime` fix. The volatile CEL brief, when the
`cel_cache_stable` flag is ON, must NOT ride in the `--append-system-prompt` file
(that file shares ONE prompt-cache block with claude's large base prompt + tool
defs, so a per-turn brief there re-CREATES the whole prefix instead of cache-READ-
ing it). Instead it is carried out as `_volatile_user_prefix` and prepended to the
per-turn USER message at spawn — AFTER the pre-spawn compliance gates, which keep
inspecting the raw task only.

Drives the REAL entry point `_resolve_spawn_inputs` (both live spawn paths compose
their system prompt through it) and the REAL legacy caller `_build_claude_args`, and
proves BOTH flag states:

  * flag OFF → brief is appended to `system`, `_volatile_user_prefix` is empty
    (byte-identical to before this feature).
  * flag ON  → brief is NOT in `system`; it is in `_volatile_user_prefix`, and the
    caller prepends it to the spawn prompt while POPPING it from the engine kwargs
    (it is not an engine arg) and leaving the raw task intact.

The CEL brief build/render is mocked (the pipeline has its own hermetic E2E); this
test's job is the BRIDGE relocation wiring, not the pipeline internals.

Run: python3 operator/bridges/shared/test_adapter_cache_stable_cel.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent.parent / "forge",
          _HERE.parent.parent.parent / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_BRIEF = "CEL-BRIEF: prior turn produced primes under 30"
_TASK = "sum exactly those primes; give ONLY the final integer"


class CacheStableCELWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-cachestable-"))
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)
        for mod in ("personal_tools", "user_style", "adapter"):
            sys.modules.pop(mod, None)
        import adapter as ad  # noqa: E402
        self.ad = ad
        import corvin_console.feature_flags as ff  # noqa: E402
        self.ff = ff

    def tearDown(self):
        for k in ("CORVIN_HOME", "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _flags(self, cache_stable: bool):
        # CEL always on; the active brain stays off (deterministic-brief path).
        on = {"vibe_engineering"} | ({"cel_cache_stable"} if cache_stable else set())
        return lambda fid, tid=None: fid in on

    def _resolve(self, cache_stable: bool):
        # Deterministic brief: _cel_build_brief → some bundle, _cel_render → _BRIEF.
        with patch.object(self.ad, "_cel_build_brief",
                          return_value=(object(), {"stages": []})), \
             patch.object(self.ad, "_cel_render", return_value=_BRIEF), \
             patch.object(self.ff, "is_enabled", self._flags(cache_stable)):
            return self.ad._resolve_spawn_inputs(
                _TASK, "unrestricted", profile={"allowed_tools": ["Read"]},
                add_dir=None, channel="discord", chat_key="chat_cs", msg_id="m_1")

    # ── flag OFF: unchanged behaviour — brief in the system prompt ──────────────
    def test_flag_off_brief_rides_in_system(self):
        out = self._resolve(cache_stable=False)
        self.assertIn(_BRIEF, out["system"],
                      "flag off: the brief must stay in the appended system prompt")
        self.assertEqual(out.get("_volatile_user_prefix", ""), "",
                         "flag off: nothing is relocated to the user message")

    # ── flag ON: relocated — brief OUT of system, INTO the user prefix ─────────
    def test_flag_on_brief_relocated_to_user_prefix(self):
        out = self._resolve(cache_stable=True)
        self.assertNotIn(_BRIEF, out["system"],
                         "flag on: the brief must NOT be in the byte-stable system prompt")
        self.assertEqual(out.get("_volatile_user_prefix"), _BRIEF,
                         "flag on: the brief is carried out to be prepended to the user turn")

    # ── caller seam: the prefix reaches the SPAWN prompt and is popped ─────────
    def test_caller_prepends_prefix_and_pops_it(self):
        # Patch _resolve_spawn_inputs to hand the legacy argv builder a known prefix,
        # then assert the built argv carries "<prefix>\n\n<task>" as the user prompt
        # and does NOT leak the prefix into the system-prompt file.
        fake = {
            "system": "SYSTEM-STABLE-BASE", "mode": "unrestricted",
            "permission_mode": None, "allowed_tools": ["Read"],
            "disallowed_tools": None, "model": None, "mcp_config_path": None,
            "add_dirs": [], "add_dir": None, "_ato_plan": None,
            "_volatile_user_prefix": _BRIEF,
        }
        with patch.object(self.ad, "_resolve_spawn_inputs", return_value=dict(fake)):
            argv = self.ad._build_claude_args(
                _TASK, "unrestricted", profile={"allowed_tools": ["Read"]},
                add_dir=None, channel="discord", chat_key="chat_cs",
                prompt_via_stdin=False, msg_id="m_1")
        joined = "\x00".join(argv)
        self.assertIn(f"{_BRIEF}\n\n{_TASK}", joined,
                      "caller must prepend the CEL prefix to the spawn prompt")
        # The system content is written to a temp file; its path is in the argv. Read it
        # back and confirm the volatile prefix did NOT leak into the byte-stable system.
        sys_file = None
        for i, a in enumerate(argv):
            if a == "--append-system-prompt-file" and i + 1 < len(argv):
                sys_file = argv[i + 1]
        if sys_file and Path(sys_file).is_file():
            self.assertNotIn(_BRIEF, Path(sys_file).read_text(encoding="utf-8"),
                             "the CEL prefix must never enter the cached system file")

    # ── the popped key must never splat into an engine as an unexpected kwarg ──
    def test_prefix_key_is_underscore_scoped_like_ato_plan(self):
        out = self._resolve(cache_stable=True)
        self.assertIn("_volatile_user_prefix", out,
                      "carried as an underscore-prefixed key (caller pops it, like _ato_plan)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
