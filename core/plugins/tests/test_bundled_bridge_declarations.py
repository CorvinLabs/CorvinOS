"""The boot path reaches the bundled bridge supervisors (ADR-0238, Stage 5).

`registry_entries.declaration_entries()` has existed since the supervisors were
written and was imported by nothing outside its own package. The classes, the
six-condition start gate and the entry generator were all complete and correct,
and no boot path ever reached any of them — the fifth instance of this ADR
series' recurring defect.

`test_bridge_supervisor.py` proves a supervisor supervises. This proves the boot
path produces one, and — the half that is easy to get wrong — that it produces
*none* on a default install.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from corvin_plugins import bootstrap as bs
from corvin_plugins.bridges.registry_entries import declaration_entries
from corvin_plugins.bridges.supervisor import BRIDGE_CHANNELS


class TestFlagOffIsTotallyQuiet(unittest.TestCase):
    """Off is the default and the shipped behaviour (CLAUDE.md § Feature Flags)."""

    def test_nothing_is_injected(self):
        with patch.object(bs, "_bundled_bridge_declarations", wraps=bs._bundled_bridge_declarations):
            with patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=False):
                self.assertEqual(bs._bundled_bridge_declarations("_default", []), [])

    def test_a_tenant_with_no_config_still_loads_nothing(self):
        # The early `if not declared and not auto_ep: return []` must still fire
        # with the flag off, or a default install would start walking the loader
        # for an empty list on every boot.
        with patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=False):
            self.assertEqual(
                bs.bootstrap_declared(
                    tenant_id="_default",
                    corvin_home=__import__("pathlib").Path("/nonexistent"),
                    tenant_config={},
                ),
                [],
            )


class TestFlagOnInjectsTheBundledSeven(unittest.TestCase):
    def setUp(self):
        p = patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_every_bundled_channel_is_declared(self):
        got = bs._bundled_bridge_declarations("_default", [])
        self.assertEqual(
            {e["id"] for e in got}, {f"{ch}-bridge" for ch in BRIDGE_CHANNELS}
        )

    def test_the_entries_are_the_ones_registry_entries_generates(self):
        # Not a re-implementation: the boot path must use the SAME generator the
        # docs and the Console panel read, or the dotted class path acquires a
        # second copy that goes stale on the first rename.
        self.assertEqual(
            bs._bundled_bridge_declarations("_default", []), declaration_entries()
        )

    def test_they_all_claim_the_bundled_boot_layer(self):
        for e in bs._bundled_bridge_declarations("_default", []):
            self.assertEqual(e["boot_layer"], "bundled", e)


class TestTheOperatorAlwaysWins(unittest.TestCase):
    """An explicit entry is the operator's reviewable statement of intent."""

    def setUp(self):
        p = patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_a_parked_bridge_is_not_re_added(self):
        # `{id: discord-bridge, config: {enabled: false}}` is how an operator
        # parks one channel. Injecting the bundled entry alongside it would
        # resurrect the bridge they switched off.
        declared = [{"id": "discord-bridge", "config": {"enabled": False}}]
        got = bs._bundled_bridge_declarations("_default", declared)
        self.assertNotIn("discord-bridge", {e["id"] for e in got})
        self.assertEqual(len(got), len(BRIDGE_CHANNELS) - 1)

    def test_an_overriding_class_path_is_not_shadowed(self):
        declared = [{"id": "slack-bridge", "class_path": "mypkg.mine:MySlack"}]
        got = bs._bundled_bridge_declarations("_default", declared)
        self.assertNotIn("slack-bridge", {e["id"] for e in got})

    def test_a_malformed_entry_does_not_crash_the_merge(self):
        # Tenant config is operator-written YAML; a stray string in the list
        # must not take the boot down.
        declared = ["not-a-dict", {"id": "email-bridge"}]
        got = bs._bundled_bridge_declarations("_default", declared)
        self.assertNotIn("email-bridge", {e["id"] for e in got})


class TestFailuresDegradeToThePreFeaturePath(unittest.TestCase):
    def test_an_unreadable_generator_leaves_bridges_as_they_were(self):
        with patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=True):
            with patch(
                "corvin_plugins.bridges.registry_entries.declaration_entries",
                side_effect=RuntimeError("boom"),
            ):
                self.assertEqual(bs._bundled_bridge_declarations("_default", []), [])

    def test_a_missing_supervisor_module_is_not_an_error(self):
        # Stripped wheel / headless core: bundled bridges are simply absent.
        import builtins

        real_import = builtins.__import__

        def _fail(name, *a, **kw):
            if "bridges.supervisor" in name:
                raise ImportError("no supervisor here")
            return real_import(name, *a, **kw)

        with patch.object(builtins, "__import__", _fail):
            self.assertEqual(bs._bundled_bridge_declarations("_default", []), [])


class TestTheLoadPathActuallyLoadsThem(unittest.TestCase):
    """`bootstrap_declared` produces supervisor instances, not just entries.

    Every other test in this module checks `_bundled_bridge_declarations` in
    isolation. A refutation round proved that insufficient: deleting the config
    injection in `bootstrap_declared` — so the entry list grew but the dict
    handed to the loader did not — left all of them green. The declarations were
    computed and thrown away, which is the same defect this whole stage exists to
    close, one level in.
    """

    def setUp(self):
        import tempfile

        p = patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from pathlib import Path

        self.home = Path(self._tmp.name)

    def _load(self, tenant_config):
        from corvin_plugins.registry import get_registry

        loaded = bs.bootstrap_declared(
            tenant_id="_default",
            corvin_home=self.home,
            tenant_config=tenant_config,
        )
        self.addCleanup(lambda: [
            get_registry().unregister(pid) for pid in loaded
        ] and None)
        return loaded

    def test_an_empty_tenant_config_still_loads_the_bundled_seven(self):
        loaded = self._load({})
        self.assertEqual(
            set(loaded), {f"{ch}-bridge" for ch in BRIDGE_CHANNELS},
            "the boot path computed the declarations and did not load them",
        )

    def test_a_parked_channel_is_absent_from_what_loaded(self):
        loaded = self._load({
            "spec": {"plugins": {"installed": [
                {"id": "discord-bridge", "config": {"enabled": False},
                 "class_path": "corvin_plugins.bridges.supervisor:DiscordBridgePlugin"},
            ]}}
        })
        # It still loads — parked means the DAEMON does not start, which is the
        # supervisor's own gate — but it loads from the OPERATOR's entry, with
        # their config, not from an injected duplicate.
        self.assertEqual(len(loaded), len(BRIDGE_CHANNELS))
        self.assertEqual(len(set(loaded)), len(loaded), "a channel loaded twice")

    def test_the_callers_config_dict_is_not_mutated(self):
        cfg: dict = {}
        self._load(cfg)
        self.assertEqual(
            cfg, {},
            "bootstrap_declared grew the caller's config dict — a side effect "
            "nobody asked for, and one that would compound across boots",
        )


class TestDeclaringIsNotStarting(unittest.TestCase):
    """ADR-0238's fail-closed defaults survive this stage.

    The injection makes supervisors LOADABLE. Whether a daemon runs is the
    six-condition gate in `supervisor.py`, which this stage does not touch —
    stated as a test because "we wired the bridges up" is exactly the sentence
    that would later be read as "and they start themselves".
    """

    def test_the_entries_carry_no_start_instruction(self):
        with patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=True):
            for e in bs._bundled_bridge_declarations("_default", []):
                self.assertNotIn("autostart", e)
                self.assertNotIn("restart", e)
                # `config` is absent unless a channel was explicitly parked.
                self.assertNotIn("config", e)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
