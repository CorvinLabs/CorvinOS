"""Per-subtask E2E for mcp_config_builder.py (Layer 30.2 / 30.3).

Covers:
  - build_mcp_specs: persona + flags → list of McpServerSpec
  - Engine-specific materialisers:
      * Claude Code: writes JSON, returns mcp_config_path
      * Codex CLI:   writes TOML in CODEX_HOME, returns env overlay
      * OpenCode:    writes JSON in working_dir, no env / kwargs
  - On-disk file modes (0o600 / 0o700)
  - Asymmetric resolve_capability env-floor wins-over-arg
  - Env-floor reads (CORVIN_DELEGATE_FORGE_ENABLED, _SKILL_FORGE_ENABLED)
  - materialise_for_engine top-level dispatcher routing
  - TOML escape function handles backslash, quote, control chars
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make plugin source importable without a venv.
_PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_DIR))

from corvin_delegate import mcp_config_builder as mcb  # noqa: E402


# ---------------------------------------------------------------------------
# build_mcp_specs
# ---------------------------------------------------------------------------


class BuildMcpSpecsTests(unittest.TestCase):

    def test_both_disabled_returns_empty(self):
        specs = mcb.build_mcp_specs(
            persona="coder",
            forge_enabled=False,
            skill_forge_enabled=False,
        )
        self.assertEqual(specs, [])

    def test_only_forge(self):
        specs = mcb.build_mcp_specs(
            persona="coder",
            forge_enabled=True,
            skill_forge_enabled=False,
        )
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, mcb.SERVER_FORGE)
        # Persona attribution lands in the env.
        self.assertEqual(specs[0].env["FORGE_PERSONA"], "coder")
        self.assertEqual(specs[0].env["CORVIN_CALLER_PERSONA"], "coder")

    def test_only_skill_forge(self):
        specs = mcb.build_mcp_specs(
            persona="research",
            forge_enabled=False,
            skill_forge_enabled=True,
        )
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, mcb.SERVER_SKILL_FORGE)
        self.assertEqual(specs[0].env["SKILL_FORGE_PERSONA"], "research")

    def test_both_enabled(self):
        specs = mcb.build_mcp_specs(
            persona="orch",
            forge_enabled=True,
            skill_forge_enabled=True,
        )
        names = {s.name for s in specs}
        self.assertEqual(names, {mcb.SERVER_FORGE, mcb.SERVER_SKILL_FORGE})

    def test_empty_persona_falls_back(self):
        specs = mcb.build_mcp_specs(
            persona="",
            forge_enabled=True,
            skill_forge_enabled=False,
        )
        self.assertEqual(specs[0].env["FORGE_PERSONA"], "delegate")

    def test_spec_command_is_running_interpreter(self):
        # Regression: the MCP server must spawn under the SAME interpreter
        # that runs this process (sys.executable), never a bare-PATH
        # "python3" — which is absent on Windows and may lack CorvinOS deps
        # even on Linux.
        specs = mcb.build_mcp_specs(
            persona="coder",
            forge_enabled=True,
            skill_forge_enabled=True,
        )
        for spec in specs:
            self.assertEqual(spec.command, sys.executable)
        # And the dataclass default likewise.
        self.assertEqual(mcb.McpServerSpec(name="x").command, sys.executable)


# ---------------------------------------------------------------------------
# Asymmetric resolve_capability
# ---------------------------------------------------------------------------


class ResolveCapabilityTests(unittest.TestCase):

    def test_env_floor_false_wins(self):
        self.assertFalse(mcb.resolve_capability(
            env_floor=False, tool_arg=True, persona_default=True))

    def test_env_floor_true_wins_over_arg_false(self):
        self.assertTrue(mcb.resolve_capability(
            env_floor=True, tool_arg=False, persona_default=False))

    def test_env_unset_uses_arg(self):
        self.assertTrue(mcb.resolve_capability(
            env_floor=None, tool_arg=True, persona_default=False))
        self.assertFalse(mcb.resolve_capability(
            env_floor=None, tool_arg=False, persona_default=True))

    def test_env_and_arg_unset_uses_persona(self):
        self.assertTrue(mcb.resolve_capability(
            env_floor=None, tool_arg=None, persona_default=True))
        self.assertFalse(mcb.resolve_capability(
            env_floor=None, tool_arg=None, persona_default=False))


class EnvFloorReadsTests(unittest.TestCase):

    def setUp(self):
        self._saved = {
            k: os.environ.get(k) for k in (
                mcb._ENV_FORGE_ENABLED,
                mcb._ENV_SKILL_FORGE_ENABLED,
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unset_returns_none(self):
        self.assertIsNone(mcb.env_floor_forge_enabled())
        self.assertIsNone(mcb.env_floor_skill_forge_enabled())

    def test_truthy(self):
        os.environ[mcb._ENV_FORGE_ENABLED] = "1"
        os.environ[mcb._ENV_SKILL_FORGE_ENABLED] = "true"
        self.assertTrue(mcb.env_floor_forge_enabled())
        self.assertTrue(mcb.env_floor_skill_forge_enabled())

    def test_falsy(self):
        os.environ[mcb._ENV_FORGE_ENABLED] = "0"
        os.environ[mcb._ENV_SKILL_FORGE_ENABLED] = "off"
        self.assertFalse(mcb.env_floor_forge_enabled())
        self.assertFalse(mcb.env_floor_skill_forge_enabled())


# ---------------------------------------------------------------------------
# Engine-specific materialisers
# ---------------------------------------------------------------------------


class MaterialiseClaudeCodeTests(unittest.TestCase):

    def test_writes_mcp_config_json_and_returns_path(self):
        specs = mcb.build_mcp_specs(
            persona="orch",
            forge_enabled=True,
            skill_forge_enabled=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kwargs = mcb.materialise_claude_code_config(
                specs=specs, tempdir=tmp_path)
            self.assertIn("mcp_config_path", kwargs)
            cfg_path = Path(kwargs["mcp_config_path"])
            self.assertTrue(cfg_path.is_file())
            data = json.loads(cfg_path.read_text("utf-8"))
            self.assertIn("mcpServers", data)
            self.assertIn("forge", data["mcpServers"])
            self.assertIn("skill_forge", data["mcpServers"])
            # Shape matches the cowork-resolver convention.
            forge_entry = data["mcpServers"]["forge"]
            self.assertIn("command", forge_entry)
            self.assertIn("args", forge_entry)
            self.assertIn("env", forge_entry)

    def test_empty_specs_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = mcb.materialise_claude_code_config(
                specs=[], tempdir=Path(tmp))
            self.assertEqual(kwargs, {})

    def test_file_mode_0o600(self):
        specs = mcb.build_mcp_specs(
            persona="orch",
            forge_enabled=True,
            skill_forge_enabled=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = mcb.materialise_claude_code_config(
                specs=specs, tempdir=Path(tmp))
            mode = (Path(kwargs["mcp_config_path"]).stat().st_mode & 0o777)
            self.assertEqual(mode, 0o600)


class MaterialiseCodexTests(unittest.TestCase):

    def test_writes_codex_home_with_config_toml(self):
        specs = mcb.build_mcp_specs(
            persona="codex_persona",
            forge_enabled=True,
            skill_forge_enabled=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = mcb.materialise_codex_config(specs=specs, tempdir=tmp_path)
            self.assertIn("CODEX_HOME", env)
            codex_home = Path(env["CODEX_HOME"])
            self.assertTrue(codex_home.is_dir())
            cfg = codex_home / "config.toml"
            self.assertTrue(cfg.is_file())
            text = cfg.read_text("utf-8")
            # Sections present for both servers.
            self.assertIn("[mcp_servers.forge]", text)
            self.assertIn("[mcp_servers.skill_forge]", text)
            # Command + args land as TOML literals. The command is the
            # running interpreter (sys.executable), not a bare "python3"
            # (Windows has no python3 on PATH; see mcp_config_builder).
            self.assertIn(f'command = "{mcb.sys.executable}"', text)
            self.assertIn("args = [", text)
            # Env entries land in inline-table form.
            self.assertIn('FORGE_PERSONA = "codex_persona"', text)

    def test_codex_home_mode_0o700(self):
        specs = mcb.build_mcp_specs(
            persona="x",
            forge_enabled=True,
            skill_forge_enabled=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = mcb.materialise_codex_config(
                specs=specs, tempdir=Path(tmp))
            codex_home = Path(env["CODEX_HOME"])
            mode = codex_home.stat().st_mode & 0o777
            self.assertEqual(mode, 0o700)
            cfg_mode = (codex_home / "config.toml").stat().st_mode & 0o777
            self.assertEqual(cfg_mode, 0o600)

    def test_empty_specs_returns_empty_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = mcb.materialise_codex_config(
                specs=[], tempdir=Path(tmp))
            self.assertEqual(env, {})


class MaterialiseOpenCodeTests(unittest.TestCase):

    def test_writes_opencode_json_in_working_dir(self):
        specs = mcb.build_mcp_specs(
            persona="oc",
            forge_enabled=True,
            skill_forge_enabled=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            mcb.materialise_opencode_config(specs=specs, working_dir=wd)
            cfg = wd / "opencode.json"
            self.assertTrue(cfg.is_file())
            data = json.loads(cfg.read_text("utf-8"))
            self.assertEqual(data.get("$schema"),
                             "https://opencode.ai/config.json")
            self.assertIn("mcp", data)
            forge = data["mcp"]["forge"]
            # OpenCode local-MCP shape: type/command/environment/enabled.
            self.assertEqual(forge["type"], "local")
            self.assertEqual(forge["enabled"], True)
            self.assertIn("environment", forge)
            self.assertIsInstance(forge["command"], list)
            # Command is interpreter + args list; interpreter is the running
            # sys.executable (cross-platform), not a literal "python3".
            self.assertEqual(forge["command"][0], sys.executable)

    def test_empty_specs_writes_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            mcb.materialise_opencode_config(specs=[], working_dir=wd)
            self.assertFalse((wd / "opencode.json").exists())


# ---------------------------------------------------------------------------
# TOML escape function
# ---------------------------------------------------------------------------


class TomlEscapeTests(unittest.TestCase):

    def test_backslash(self):
        self.assertEqual(mcb._toml_escape_str("a\\b"), "a\\\\b")

    def test_double_quote(self):
        self.assertEqual(mcb._toml_escape_str('say "hi"'),
                         'say \\"hi\\"')

    def test_newline_tab_cr(self):
        self.assertEqual(mcb._toml_escape_str("a\nb\tc\rd"),
                         "a\\nb\\tc\\rd")


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


class MaterialiseForEngineTests(unittest.TestCase):

    def setUp(self):
        self.specs = mcb.build_mcp_specs(
            persona="orch",
            forge_enabled=True,
            skill_forge_enabled=True,
        )

    def test_claude_code_returns_spawn_kwargs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = mcb.materialise_for_engine(
                engine_id=mcb.ENGINE_CLAUDE_CODE,
                specs=self.specs,
                tempdir=Path(tmp),
            )
            self.assertIn("mcp_config_path", r["spawn_kwargs"])
            self.assertEqual(r["env_overlay"], {})
            self.assertEqual(set(r["mcp_servers"]),
                             {"forge", "skill_forge"})

    def test_codex_returns_env_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = mcb.materialise_for_engine(
                engine_id=mcb.ENGINE_CODEX,
                specs=self.specs,
                tempdir=Path(tmp),
            )
            self.assertIn("CODEX_HOME", r["env_overlay"])
            self.assertEqual(r["spawn_kwargs"], {})

    def test_opencode_returns_neither(self):
        """OpenCode picks up opencode.json from cwd; no spawn_kwargs
        / env_overlay needed beyond the file write."""
        with tempfile.TemporaryDirectory() as tmp:
            r = mcb.materialise_for_engine(
                engine_id=mcb.ENGINE_OPENCODE,
                specs=self.specs,
                tempdir=Path(tmp),
            )
            self.assertEqual(r["spawn_kwargs"], {})
            self.assertEqual(r["env_overlay"], {})
            # File landed in tempdir.
            self.assertTrue((Path(tmp) / "opencode.json").is_file())

    def test_opencode_uses_working_dir_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp_a, \
             tempfile.TemporaryDirectory() as tmp_b:
            r = mcb.materialise_for_engine(
                engine_id=mcb.ENGINE_OPENCODE,
                specs=self.specs,
                tempdir=Path(tmp_a),
                working_dir=Path(tmp_b),
            )
            self.assertFalse((Path(tmp_a) / "opencode.json").exists())
            self.assertTrue((Path(tmp_b) / "opencode.json").is_file())

    def test_unknown_engine_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = mcb.materialise_for_engine(
                engine_id="future_engine",
                specs=self.specs,
                tempdir=Path(tmp),
            )
            self.assertEqual(r["spawn_kwargs"], {})
            self.assertEqual(r["env_overlay"], {})
            self.assertEqual(r["mcp_servers"], [])

    def test_empty_specs_short_circuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = mcb.materialise_for_engine(
                engine_id=mcb.ENGINE_CLAUDE_CODE,
                specs=[],
                tempdir=Path(tmp),
            )
            self.assertEqual(r["spawn_kwargs"], {})
            self.assertEqual(r["env_overlay"], {})
            self.assertEqual(r["mcp_servers"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
