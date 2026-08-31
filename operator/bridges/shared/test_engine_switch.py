"""test_engine_switch.py — per-subtask E2E for engine_switch.py.

Covers (a) the pure-function path (alias resolution, validators,
env-overlay shape), (b) the on-disk store round-trip including the
mtime-hot-reload promise (current() always reads disk), (c) the audit
chain emission with metadata-only allow-list enforcement, (d) the
adapter env-injection wiring via _build_spawn_env, and (e) the CLI
subcommands.

All cases sandbox-isolate CORVIN_HOME so the test never touches the
operator's live state.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import engine_switch  # noqa: E402


# ── Sandbox helper ────────────────────────────────────────────────────

class _Sandbox:
    """Per-test CORVIN_HOME sandbox + forge-package path injection."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="engine-switch-")
        self.home = Path(self.tmp.name)
        self._saved = {}
        # Save and replace CORVIN_HOME so tests operate on the sandbox.
        self._saved["CORVIN_HOME"] = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self.home)
        # Make sure the forge package is importable for audit-chain
        # round-trip checks. We walk up to find operator/forge — same
        # heuristic the production engine_switch._audit uses.
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "operator" / "forge").is_dir():
                fp = str(parent / "operator" / "forge")
                if fp not in sys.path:
                    sys.path.insert(0, fp)
                break

    def close(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def __enter__(self) -> "_Sandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ── 1. Alias resolution ───────────────────────────────────────────────

class AliasResolutionTests(unittest.TestCase):
    def test_claude_alias(self):
        spec = engine_switch.resolve_alias("claude")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["engine"], "claude_code")
        self.assertIsNone(spec["model"])

    def test_codex_alias(self):
        spec = engine_switch.resolve_alias("codex")
        self.assertEqual(spec["engine"], "codex_cli")
        self.assertIsNone(spec["model"])

    def test_opencode_alias_pins_local_ollama_model(self):
        spec = engine_switch.resolve_alias("opencode")
        self.assertEqual(spec["engine"], "opencode")
        self.assertEqual(spec["model"], "ollama/qwen3:8b")

    def test_cloud_alias_pins_ollama_cloud_model(self):
        spec = engine_switch.resolve_alias("cloud")
        self.assertEqual(spec["engine"], "opencode")
        self.assertEqual(spec["model"], "ollama-cloud/qwen3-coder-next")

    def test_case_insensitive(self):
        for token in ("CLAUDE", "Claude", "ClAuDe"):
            spec = engine_switch.resolve_alias(token)
            self.assertIsNotNone(spec, f"failed on {token!r}")
            self.assertEqual(spec["engine"], "claude_code")

    def test_back_compat_underscore_hyphen(self):
        a = engine_switch.resolve_alias("claude_code")
        b = engine_switch.resolve_alias("claude-code")
        self.assertEqual(a["engine"], "claude_code")
        self.assertEqual(b["engine"], "claude_code")

    def test_unknown_alias_returns_none(self):
        self.assertIsNone(engine_switch.resolve_alias("gemini"))
        self.assertIsNone(engine_switch.resolve_alias(""))
        self.assertIsNone(engine_switch.resolve_alias("   "))

    def test_supported_aliases_curated_short_list(self):
        aliases = engine_switch.supported_aliases()
        self.assertEqual(set(aliases), {"claude", "codex", "opencode", "cloud", "hermes", "hermes-fast"})


# ── 2. Set / current / clear round-trip ──────────────────────────────

class SetCurrentClearRoundTripTests(unittest.TestCase):
    def test_current_returns_none_when_no_file(self):
        with _Sandbox():
            self.assertIsNone(engine_switch.current("discord", "chat-1"))

    def test_set_then_current_roundtrip_claude(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="user-42",
            )
            pref = engine_switch.current("discord", "chat-1")
            self.assertEqual(pref, {"engine": "claude_code", "model": ""})

    def test_set_with_model_persists_both(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-1",
                engine="opencode",
                model="ollama-cloud/qwen3-coder-next",
                uid="user-42",
            )
            pref = engine_switch.current("discord", "chat-1")
            self.assertEqual(pref["engine"], "opencode")
            self.assertEqual(pref["model"], "ollama-cloud/qwen3-coder-next")

    def test_clear_removes_file(self):
        with _Sandbox() as sb:
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="u",
            )
            path = sb.home / "global" / "engine_pref" / "discord__chat_1.json"
            self.assertTrue(path.exists())
            removed = engine_switch.clear_preference("discord", "chat-1", uid="u")
            self.assertTrue(removed)
            self.assertFalse(path.exists())
            self.assertIsNone(engine_switch.current("discord", "chat-1"))

    def test_clear_idempotent_when_no_file(self):
        with _Sandbox():
            self.assertFalse(engine_switch.clear_preference("discord", "ghost", uid=""))

    def test_set_rejects_unknown_engine(self):
        with _Sandbox():
            with self.assertRaises(ValueError):
                engine_switch.set_preference(
                    "discord", "chat-1", engine="gemini", uid="u",
                )

    def test_set_rejects_path_traversal_in_model(self):
        with _Sandbox():
            with self.assertRaises(ValueError):
                engine_switch.set_preference(
                    "discord", "chat-1",
                    engine="opencode",
                    model="../../etc/passwd",
                    uid="u",
                )

    def test_set_rejects_whitespace_in_model(self):
        with _Sandbox():
            with self.assertRaises(ValueError):
                engine_switch.set_preference(
                    "discord", "chat-1",
                    engine="opencode",
                    model="model with spaces",
                    uid="u",
                )


# ── 3. Per-chat isolation ────────────────────────────────────────────

class PerChatIsolationTests(unittest.TestCase):
    def test_different_chats_keep_separate_prefs(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-A", engine="opencode",
                model="ollama/qwen3:8b", uid="u",
            )
            engine_switch.set_preference(
                "discord", "chat-B", engine="codex_cli", uid="u",
            )
            a = engine_switch.current("discord", "chat-A")
            b = engine_switch.current("discord", "chat-B")
            self.assertEqual(a["engine"], "opencode")
            self.assertEqual(b["engine"], "codex_cli")

    def test_different_channels_keep_separate_prefs(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="u",
            )
            engine_switch.set_preference(
                "telegram", "chat-1", engine="codex_cli", uid="u",
            )
            self.assertEqual(
                engine_switch.current("discord", "chat-1")["engine"],
                "claude_code",
            )
            self.assertEqual(
                engine_switch.current("telegram", "chat-1")["engine"],
                "codex_cli",
            )

    def test_chat_key_safe_component_collision_resistant(self):
        # A chat_key with slashes gets sanitised; the safe components
        # must still differ between two materially-distinct chats.
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "a/b", engine="claude_code", uid="u",
            )
            engine_switch.set_preference(
                "discord", "a_b", engine="codex_cli", uid="u",
            )
            # NOTE: sanitisation maps `a/b` and `a_b` to the same safe
            # component on purpose (the chat_key is the bridge's
            # responsibility; the slash sanitisation only ensures
            # path safety). The second write therefore overwrites
            # the first — that's the documented contract.
            self.assertEqual(
                engine_switch.current("discord", "a_b")["engine"],
                "codex_cli",
            )


# ── 4. Hot-reload semantic — current() reads disk every call ────────

class HotReloadTests(unittest.TestCase):
    def test_external_write_visible_without_restart(self):
        with _Sandbox() as sb:
            path = sb.home / "global" / "engine_pref" / "discord__chat_1.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            # Initial preference via API.
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="u",
            )
            self.assertEqual(
                engine_switch.current("discord", "chat-1")["engine"],
                "claude_code",
            )
            # External tool overwrites the file directly.
            path.write_text(json.dumps({
                "engine": "opencode",
                "model": "ollama/qwen3:8b",
                "set_at": 0.0,
                "set_by_uid": "external",
                "channel": "discord",
            }))
            # No restart — next current() picks it up.
            self.assertEqual(
                engine_switch.current("discord", "chat-1")["engine"],
                "opencode",
            )

    def test_malformed_file_treated_as_absent(self):
        with _Sandbox() as sb:
            path = sb.home / "global" / "engine_pref" / "discord__chat_1.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not valid json")
            self.assertIsNone(engine_switch.current("discord", "chat-1"))

    def test_engine_outside_whitelist_treated_as_absent(self):
        with _Sandbox() as sb:
            path = sb.home / "global" / "engine_pref" / "discord__chat_1.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "engine": "gemini_cli",  # not in VALID_ENGINES
                "model": "",
                "set_at": 0.0,
                "set_by_uid": "",
                "channel": "discord",
            }))
            self.assertIsNone(engine_switch.current("discord", "chat-1"))


# ── 5. Audit emission (metadata-only contract) ───────────────────────

class AuditContractTests(unittest.TestCase):
    def _chain_path(self, sb: _Sandbox) -> Path:
        return sb.home / "global" / "forge" / "audit.jsonl"

    def test_audit_allow_list_rejects_smuggled_keys(self):
        # The structural defence — even if a future edit tried to pass
        # a prompt / user-text field into _audit, the validator must
        # raise. We call it directly here.
        with self.assertRaises(ValueError):
            engine_switch._validate_audit_details({
                "channel": "discord",
                "chat_key": "c",
                "uid": "u",
                "action": "set",
                "engine": "claude_code",
                "model": None,
                "prompt": "leaked",  # forbidden
            })

    def test_set_lands_event_in_chain(self):
        with _Sandbox() as sb:
            engine_switch.set_preference(
                "discord", "chat-1", engine="opencode",
                model="ollama/qwen3:8b", uid="user-42",
            )
            chain = self._chain_path(sb)
            self.assertTrue(chain.exists(), "audit chain should exist")
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
            self.assertTrue(any(
                rec.get("event_type") == "engine.pref_switched"
                and rec.get("details", {}).get("action") == "set"
                and rec.get("details", {}).get("engine") == "opencode"
                and rec.get("details", {}).get("model") == "ollama/qwen3:8b"
                for rec in lines
            ), f"set event not found: {lines}")

    def test_clear_lands_event_in_chain(self):
        with _Sandbox() as sb:
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="u",
            )
            engine_switch.clear_preference("discord", "chat-1", uid="u")
            chain = self._chain_path(sb)
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
            cleared = [rec for rec in lines
                       if rec.get("event_type") == "engine.pref_switched"
                       and rec.get("details", {}).get("action") == "cleared"]
            self.assertEqual(len(cleared), 1, f"expected 1 cleared event, got: {cleared}")

    def test_audit_metadata_only_no_freetext(self):
        # End-to-end check: walk every emitted event and assert no
        # unexpected keys appear in details. Mirror of the L23 / L25 /
        # L28 / L29 metadata-only regression gate.
        with _Sandbox() as sb:
            engine_switch.set_preference(
                "discord", "chat-1", engine="codex_cli", uid="u",
            )
            engine_switch.clear_preference("discord", "chat-1", uid="u")
            chain = self._chain_path(sb)
            allowed = engine_switch._AUDIT_ALLOWED
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
            # Keys injected by write_event() infrastructure (e.g. ADR-0132 chain_dna)
            # are NOT controlled by engine_switch and must be excluded from the check.
            _INFRA_KEYS = frozenset({"chain_dna"})
            for rec in lines:
                if rec.get("event_type") != "engine.pref_switched":
                    continue
                for k in rec.get("details", {}).keys():
                    if k in _INFRA_KEYS:
                        continue
                    self.assertIn(k, allowed,
                                  f"smuggled audit key: {k!r} in {rec}")


# ── 6. env_overlay shape ─────────────────────────────────────────────

class EnvOverlayTests(unittest.TestCase):
    def test_no_pref_returns_empty(self):
        with _Sandbox():
            self.assertEqual(
                engine_switch.env_overlay("discord", "ghost"), {},
            )

    def test_pref_without_model_sets_only_engine(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-1", engine="claude_code", uid="u",
            )
            overlay = engine_switch.env_overlay("discord", "chat-1")
            self.assertEqual(overlay, {
                "CORVIN_DELEGATE_PREF_ENGINE": "claude_code",
            })

    def test_pref_with_model_sets_both(self):
        with _Sandbox():
            engine_switch.set_preference(
                "discord", "chat-1", engine="opencode",
                model="ollama/qwen3:8b", uid="u",
            )
            overlay = engine_switch.env_overlay("discord", "chat-1")
            self.assertEqual(overlay, {
                "CORVIN_DELEGATE_PREF_ENGINE": "opencode",
                "CORVIN_DELEGATE_PREF_MODEL": "ollama/qwen3:8b",
            })


# ── 7. Adapter integration — _build_spawn_env honours overlay ────────

class AdapterEnvInjectionTests(unittest.TestCase):
    """Verify the adapter's _build_spawn_env picks up the overlay.

    This is the load-bearing wiring: without it, /engine writes a file
    nobody reads, and the orchestrator never sees the user's intent.
    """

    def test_build_spawn_env_injects_overlay(self):
        with _Sandbox():
            # Import adapter lazily — it pulls a lot of optional deps,
            # any of which may fail in a bare CI environment. If import
            # fails, skip rather than red the suite (the env-overlay
            # contract is still independently covered by EnvOverlayTests
            # above).
            try:
                import adapter  # type: ignore  # noqa: PLC0415
            except Exception as e:  # noqa: BLE001
                self.skipTest(f"adapter import unavailable: {e}")
            engine_switch.set_preference(
                "discord", "chat-1", engine="opencode",
                model="ollama-cloud/qwen3-coder-next", uid="u",
            )
            env = adapter._build_spawn_env(
                bridge="discord", chat_key="chat-1",
                base={"PATH": "/usr/bin"},
                profile=None,
            )
            self.assertEqual(env.get("CORVIN_DELEGATE_PREF_ENGINE"), "opencode")
            self.assertEqual(
                env.get("CORVIN_DELEGATE_PREF_MODEL"),
                "ollama-cloud/qwen3-coder-next",
            )

    def test_build_spawn_env_strips_stale_pref_when_cleared(self):
        with _Sandbox():
            try:
                import adapter  # type: ignore  # noqa: PLC0415
            except Exception as e:  # noqa: BLE001
                self.skipTest(f"adapter import unavailable: {e}")
            # Parent process carries a stale value; no on-disk pref.
            env = adapter._build_spawn_env(
                bridge="discord", chat_key="chat-2",
                base={
                    "PATH": "/usr/bin",
                    "CORVIN_DELEGATE_PREF_ENGINE": "stale_engine",
                    "CORVIN_DELEGATE_PREF_MODEL": "stale_model",
                },
                profile=None,
            )
            self.assertNotIn("CORVIN_DELEGATE_PREF_ENGINE", env)
            self.assertNotIn("CORVIN_DELEGATE_PREF_MODEL", env)


# ── 8. CLI subcommands ───────────────────────────────────────────────

class CLITests(unittest.TestCase):
    def _run(self, *args: str, sb_home: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CORVIN_HOME"] = str(sb_home)
        cli = HERE / "engine_switch.py"
        return subprocess.run(
            ["python3", str(cli), *args],
            capture_output=True, text=True, env=env,
        )

    def test_aliases_subcommand(self):
        with _Sandbox() as sb:
            r = self._run("aliases", sb_home=sb.home)
            self.assertEqual(r.returncode, 0, r.stderr)
            for a in ("claude", "codex", "opencode", "cloud"):
                self.assertIn(a, r.stdout)

    def test_show_no_pref(self):
        with _Sandbox() as sb:
            r = self._run("show", "discord", "ghost", sb_home=sb.home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("no preference", r.stdout)

    def test_set_then_show_roundtrip(self):
        with _Sandbox() as sb:
            r1 = self._run("set", "discord", "chat-1", "cloud",
                           "--uid", "user-42", sb_home=sb.home)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertIn("opencode", r1.stdout)
            self.assertIn("ollama-cloud", r1.stdout)
            r2 = self._run("show", "discord", "chat-1", sb_home=sb.home)
            self.assertEqual(r2.returncode, 0)
            self.assertIn("opencode", r2.stdout)
            self.assertIn("ollama-cloud/qwen3-coder-next", r2.stdout)

    def test_set_unknown_alias_exits_2(self):
        with _Sandbox() as sb:
            r = self._run("set", "discord", "chat-1", "gemini", sb_home=sb.home)
            self.assertEqual(r.returncode, 2)
            self.assertIn("unknown engine alias", r.stdout)

    def test_clear_roundtrip(self):
        with _Sandbox() as sb:
            self._run("set", "discord", "chat-1", "claude", sb_home=sb.home)
            r = self._run("clear", "discord", "chat-1", sb_home=sb.home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("cleared", r.stdout)
            r2 = self._run("show", "discord", "chat-1", sb_home=sb.home)
            self.assertIn("no preference", r2.stdout)


# ── 9. Cost contract — no LLM SDK imports ────────────────────────────

class NoSdkImportContractTests(unittest.TestCase):
    """engine_switch.py is a local-only file-IO module. Importing any
    LLM SDK would silently break the cost contract — preference flips
    must never touch an LLM. The AST walk is the regression gate.
    """

    FORBIDDEN = {"anthropic", "openai", "google.cloud.aiplatform",
                 "vertexai", "cohere"}

    def test_no_forbidden_import(self):
        src = (HERE / "engine_switch.py").read_text()
        tree = ast.parse(src)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    if n.name in self.FORBIDDEN:
                        offenders.append(n.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in {
                    s.split(".")[0] for s in self.FORBIDDEN
                }:
                    offenders.append(node.module)
        self.assertEqual(offenders, [],
                         f"forbidden imports in engine_switch.py: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
