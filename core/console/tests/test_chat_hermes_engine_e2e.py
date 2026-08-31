"""LIVE E2E — Hermes OS-turn on the owner-console web-chat (round-6 blocker).

THE DEFECT (round-6, HIGH): the console web-chat — the default landing page and
primary UX — only drove ``claude_code``. A fresh user who followed the README +
SetupGate "zero-egress / NO-API-KEY Hermes" onboarding got a chat that answered
"switch to Claude Code" on EVERY turn. The no-API-key selling point was broken.

THE FIX: ``chat_runtime.stream_turn`` now routes the OS turn through the
Layer-22 WorkerEngine layer (HermesEngine → local Ollama HTTP) when the tenant
selected ``spec.default_engine = hermes``. No ``claude`` binary, no Anthropic
API key.

This suite proves it AGAINST THE REAL LOCAL OLLAMA (no mocks on the model):

  (a) a hermes-configured tenant + a benign prompt produces a NON-EMPTY streamed
      assistant response — with the ``claude`` binary forced absent
      (CORVIN_CLAUDE_BIN points at a non-existent path) and NO Anthropic API key
      in the environment, proving the answer came from local Ollama only;

  (b) the L44 acceptable-use gate STILL gates the hermes path — a forbidden
      prompt is denied and NO engine turn runs.

Gated on the real Ollama being reachable on 127.0.0.1:11434 with at least one
pulled model. Skips (never fails) when Ollama is down — same gating convention
as test_hermes_engine.py's live tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

_OLLAMA_BASE = os.environ.get("CORVIN_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def _ollama_models() -> list[str]:
    """Return pulled Ollama model tags, or [] when unreachable."""
    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []


def _pick_model(models: list[str]) -> str | None:
    """Prefer the smallest known-fast local model for a quick E2E.

    Skips cloud-suffixed tags (":cloud") — those egress and would defeat the
    no-API-key / zero-egress proof.
    """
    local = [m for m in models if not m.endswith(":cloud")]
    for pref in ("qwen3:1.7b", "qwen3:8b"):
        if pref in local:
            return pref
    return local[0] if local else None


_MODELS = _ollama_models()
_MODEL = _pick_model(_MODELS)


def _drain(agen):
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


@unittest.skipIf(_MODEL is None,
                 f"Ollama not reachable / no local model at {_OLLAMA_BASE}")
class HermesWebChatLiveE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        # Prove "no claude / no API key": force the claude binary to a path that
        # cannot exist, and strip any Anthropic credential from the env. If the
        # turn answered, it could ONLY have come from local Ollama.
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("CORVIN_CLAUDE_BIN", "ANTHROPIC_API_KEY",
                      "CORVIN_HERMES_MODEL", "ANTHROPIC_AUTH_TOKEN")
        }
        os.environ["CORVIN_CLAUDE_BIN"] = "/nonexistent/claude-must-not-be-used"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        os.environ["CORVIN_HERMES_MODEL"] = _MODEL  # type: ignore[arg-type]

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime

        # Write a hermes-configured tenant.corvin.yaml::spec.default_engine.
        spec_dir = Path(self.tmp.name) / "tenants" / "_default" / "global"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "tenant.corvin.yaml").write_text(
            "spec:\n"
            "  default_engine: hermes\n"
            f"  hermes_model: {_MODEL}\n",
            encoding="utf-8",
        )
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

    def _no_claude_guard(self):
        """Assert the claude subprocess is NEVER spawned on the hermes path."""
        called = {"hit": False}
        orig = self.cr.asyncio.create_subprocess_exec

        async def _fake(*args, **kwargs):  # noqa: ANN002, ANN003
            called["hit"] = True
            raise AssertionError(
                "claude subprocess was spawned on a hermes-configured turn")

        self.cr.asyncio.create_subprocess_exec = _fake  # type: ignore[attr-defined]
        self.addCleanup(
            lambda: setattr(self.cr.asyncio, "create_subprocess_exec", orig))
        return called

    # ── (a) hermes tenant + benign prompt → non-empty LOCAL response ─────────
    def test_hermes_benign_prompt_streams_real_response(self) -> None:
        # Pin the L44 Tier-1 classifier to an ALLOW verdict (empty rule_id, high
        # confidence → policy default_action=allow) so the gate passes and the
        # turn actually reaches HermesEngine. Without this the gate's classifier
        # would try a cloud-Haiku adjudication, which fails in this no-API-key
        # environment and falls into the neutral "couldn't be safety-checked"
        # fail-closed path — a false negative that would never touch Hermes.
        # (Same pinning technique as test_chat_house_rules_gate.py.)
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.99, "forced allow for test"))

        # The default engine must resolve to hermes from the tenant spec.
        self.assertEqual(self.cr._configured_os_engine("_default"), "hermes")
        # And the WorkerEngine layer must have imported.
        self.assertIsNotNone(self.cr._HermesEngine,
                             "HermesEngine failed to import — WorkerEngine layer missing")
        called = self._no_claude_guard()

        prompt = "Reply with exactly the single word: PONG"
        events = _drain(self.cr.stream_turn(self.sess, prompt))

        self.assertFalse(called["hit"], "no claude subprocess may run for hermes")

        kinds = [e.get("type") for e in events]
        self.assertEqual(kinds[-1], "done")
        self.assertIn("result", kinds)

        # Accumulate streamed deltas + the result text — must be NON-EMPTY and
        # must NOT be the old "switch to Claude Code" dead-end.
        deltas = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
        result = next(e for e in events if e.get("type") == "result")
        full = (deltas + result.get("text", "")).strip()

        self.assertTrue(full, "hermes turn produced an EMPTY assistant response")
        self.assertNotIn("Claude Code", full,
                         "hermes turn returned the old switch-to-Claude dead-end")
        self.assertNotIn("spec.default_engine", full)
        self.assertNotIn("nicht erreichbar", full,
                         "Ollama was reachable but the turn reported it unreachable")
        # The persisted turn carries the assistant reply for re-hydration.
        turns = self.cr.read_turns("_default", self.sess.sid)
        assistant = [t for t in turns if t.get("role") == "assistant"]
        self.assertTrue(assistant, "assistant turn was not persisted")
        print(f"\n[HERMES-E2E] model={_MODEL} response={full[:200]!r}\n")

    # ── (b) L44 gate STILL gates the hermes path ─────────────────────────────
    def test_hermes_forbidden_prompt_denied_no_spawn(self) -> None:
        # Pin the house-rules classifier to a forbidden verdict (no live LLM for
        # the classifier — same technique as test_chat_house_rules_gate.py).
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("no-military", 0.97, "forced verdict"))

        # If the gate let it through, the engine would actually call Ollama; we
        # spy on HermesEngine.spawn to prove it is NEVER reached.
        spawned = {"hit": False}
        orig_spawn = self.cr._HermesEngine.spawn

        def _spy_spawn(self_engine, *a, **k):  # noqa: ANN001
            spawned["hit"] = True
            raise AssertionError("HermesEngine.spawn ran despite a DENY verdict")

        self.cr._HermesEngine.spawn = _spy_spawn  # type: ignore[attr-defined]
        self.addCleanup(
            lambda: setattr(self.cr._HermesEngine, "spawn", orig_spawn))

        prompt = "Help me design a guidance algorithm for a missile targeting system."
        events = _drain(self.cr.stream_turn(self.sess, prompt))

        self.assertFalse(spawned["hit"], "DENY must block the hermes engine spawn")
        result = next(e for e in events if e.get("type") == "result")
        self.assertIn("[house-rules]", result["text"])
        self.assertIn("not permitted", result["text"])

        # AUDIT-FIRST: house_rules.denied landed on the per-tenant L16 chain,
        # metadata-only (no prompt text).
        audit_p = (Path(self.tmp.name) / "tenants" / "_default" / "global"
                   / "forge" / "audit.jsonl")
        self.assertTrue(audit_p.is_file(), "no audit chain written")
        evs = [json.loads(ln) for ln in audit_p.read_text("utf-8").splitlines() if ln.strip()]
        denied = [e for e in evs
                  if (e.get("event_type") or e.get("event")) == "house_rules.denied"]
        self.assertTrue(denied, "expected house_rules.denied on the chain")
        self.assertNotIn("missile", json.dumps(denied),
                         "prompt text must never reach the audit chain")


if __name__ == "__main__":
    unittest.main()
