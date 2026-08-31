"""Tests for `corvin config set telemetry.*` -- adversarial review finding.

Before this fix, the exact command the software itself prints as "how to
opt out" (serve_backend.py's one-time telemetry notice: "To opt out: corvin
config set telemetry.ping_enabled false") silently wrote into
~/.config/corvin-launcher/config.json, a file htrace_consent.py::ping_enabled()
never reads -- the documented opt-out was a complete no-op. `telemetry.*`
keys must instead land in <corvin_home>/tenants/_default/global/
tenant.corvin.yaml under spec.telemetry.<subkey>, the exact file/path
ping_enabled() actually consults.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("ops/launcher", "operator/bridges/shared", "operator/forge", "core/console"):
    sys.path.insert(0, str(_REPO / p))

from corvin import cli  # noqa: E402


class TelemetryConfigSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(Path(self._tmpdir.name) / ".corvin")

    def tearDown(self) -> None:
        if self._orig_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._orig_home
        self._tmpdir.cleanup()

    def test_ping_enabled_false_actually_reaches_the_gate_ping_enabled_reads(self) -> None:
        from forge.paths import corvin_home
        from corvin_console.aco.htrace_consent import ping_enabled

        home = corvin_home()
        self.assertTrue(ping_enabled(home), "must default ON before any config exists")

        rc = cli.cmd_config_set(
            argparse.Namespace(key="telemetry.ping_enabled", value="false")
        )
        self.assertEqual(rc, 0)
        self.assertFalse(
            ping_enabled(home),
            "the exact command the software tells users to run must actually opt out",
        )

    def test_re_enabling_flips_back_to_true(self) -> None:
        from forge.paths import corvin_home
        from corvin_console.aco.htrace_consent import ping_enabled

        home = corvin_home()
        cli.cmd_config_set(argparse.Namespace(key="telemetry.ping_enabled", value="false"))
        self.assertFalse(ping_enabled(home))
        cli.cmd_config_set(argparse.Namespace(key="telemetry.ping_enabled", value="true"))
        self.assertTrue(ping_enabled(home))

    def test_writes_to_tenant_corvin_yaml_not_launcher_config_json(self) -> None:
        from forge.paths import corvin_home
        from corvin_console.aco.htrace_consent import _tenant_cfg_path

        cli.cmd_config_set(argparse.Namespace(key="telemetry.ping_enabled", value="false"))
        cfg_path = _tenant_cfg_path(corvin_home())
        self.assertTrue(cfg_path.exists())
        self.assertIn("tenant.corvin.yaml", str(cfg_path))

        launcher_config = Path.home() / ".config" / "corvin-launcher" / "config.json"
        if launcher_config.exists():
            self.assertNotIn("telemetry", launcher_config.read_text())

    def test_accepts_various_falsy_string_forms(self) -> None:
        from forge.paths import corvin_home
        from corvin_console.aco.htrace_consent import ping_enabled

        home = corvin_home()
        for falsy in ("false", "False", "no", "0", "off"):
            cli.cmd_config_set(argparse.Namespace(key="telemetry.ping_enabled", value="true"))
            self.assertTrue(ping_enabled(home))
            cli.cmd_config_set(argparse.Namespace(key="telemetry.ping_enabled", value=falsy))
            self.assertFalse(ping_enabled(home), f"{falsy!r} must be treated as opt-out")

    def test_real_cli_parser_accepts_telemetry_ping_enabled(self) -> None:
        """Every test above calls cmd_config_set() directly with a hand-built
        argparse.Namespace — that bypassed the real parser entirely and gave
        false confidence. The actual `corvin config set telemetry.ping_enabled
        false` command line failed with argparse's own "invalid choice" usage
        error (exit code 2) BEFORE cmd_config_set ever ran, because the `key`
        argument had `choices=["ollama-url", "model", "bridge", "image"]`.
        This test goes through the real parser to close that gap."""
        from forge.paths import corvin_home
        from corvin_console.aco.htrace_consent import ping_enabled

        parser = cli._build_parser()
        args = parser.parse_args(["config", "set", "telemetry.ping_enabled", "false"])
        self.assertEqual(args.key, "telemetry.ping_enabled")
        rc = cli.cmd_config_set(args)
        self.assertEqual(rc, 0)
        self.assertFalse(ping_enabled(corvin_home()))

    def test_real_cli_parser_still_accepts_the_original_four_keys(self) -> None:
        parser = cli._build_parser()
        for key in ("ollama-url", "model", "bridge", "image"):
            args = parser.parse_args(["config", "set", key, "x"])
            self.assertEqual(args.key, key)

    def test_non_telemetry_keys_still_use_launcher_config(self) -> None:
        """Regression guard: the telemetry special-case must not swallow the
        normal ollama-url/model/bridge/image keys, which belong in the
        corvin-launcher config.json, not tenant.corvin.yaml. Patches the
        launcher config path so this test never touches the real
        ~/.config/corvin-launcher/config.json on the machine running it."""
        from corvin import config as launcher_cfg

        fake_path = Path(self._tmpdir.name) / "launcher-config.json"
        orig_path = launcher_cfg._CONFIG_PATH
        launcher_cfg._CONFIG_PATH = fake_path
        try:
            rc = cli.cmd_config_set(argparse.Namespace(key="model", value="qwen3:14b"))
            self.assertEqual(rc, 0)
            self.assertEqual(launcher_cfg.get("model"), "qwen3:14b")
            self.assertTrue(fake_path.exists())
        finally:
            launcher_cfg._CONFIG_PATH = orig_path


if __name__ == "__main__":
    unittest.main()
