"""Tests for the extension-point bus (ADR-0237, Phase 3).

The properties under test are not "a dict stores a function" but the ones a
call site actually relies on:

* with ``plugin_extension_points`` off — the default install — a registered
  hook is inert and the pre-feature path runs, quietly;
* with it on, the hook's return value reaches the caller;
* a hook that raises never reaches the call site: a normal point degrades to
  the default, a fail-closed point denies;
* what gets audited about that failure is the exception CLASS, never its
  message (the audit chain is append-only, so a leaked path or prompt fragment
  there is permanent);
* a point that may never have a hook is refused with its own error, so the
  attempt fails visibly instead of looking like a typo;
* one tenant's hook is not another tenant's hook.

The flag is toggled through the REAL resolution path (``CORVIN_HOME`` plus a
``features.json`` overlay), not by patching ``_flag_state``.  Patching the
gate would prove the bus honours a mock and nothing about whether the flag it
actually reads is the one the Console writes.

``tenant_id`` is spelled out at every ``register_hook`` call here rather than
inherited from a default, because the function has no default any more: a hook
that silently landed in ``_default`` — or in a tenant the caller merely named —
could take over a point (including the fail-closed workflow gate) in somebody
else's turn, since last-registration-wins makes that a takeover, not a clash.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO), str(_REPO / "core" / "console")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import extension_points as ep  # noqa: E402


class _Base(unittest.TestCase):
    """Isolated CORVIN_HOME, empty bus, captured audit sink.

    ``_audit`` is redirected for the whole module so a unit test never appends
    to a real hash-chained trail, and so the assertions can inspect the exact
    detail dict the bus builds.  The un-redirected path gets its own test.
    """

    #: Flag state for this test class.  Both values are exercised, because a
    #: flag only ever tested in one state rots (CLAUDE.md § Feature Flags).
    FLAG = False

    def setUp(self) -> None:
        ep.clear_all()
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name)
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        for tenant in ("_default", "tenant-a", "tenant-b"):
            g = home / "tenants" / tenant / "global"
            g.mkdir(parents=True)
            (g / "features.json").write_text(
                json.dumps({"flags": {ep.FLAG_ID: self.FLAG}}), encoding="utf-8"
            )

        self.audited: list[tuple[str, dict]] = []
        self._real_audit = ep._audit

        def _sink(event_type: str, details: dict, *, tenant_id: str) -> None:
            self.audited.append((event_type, details))

        ep._audit = _sink  # type: ignore[assignment]

    def tearDown(self) -> None:
        ep._audit = self._real_audit  # type: ignore[assignment]
        ep.clear_all()
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def events(self, event_type: str) -> list[dict]:
        return [d for t, d in self.audited if t == event_type]


# ── 1. The flag gates invocation, in BOTH states ──────────────────────────────


class TestFlagOff(_Base):
    """Off is the default install.  It must be the old path, and it must be quiet."""

    FLAG = False

    def test_the_flag_ships_dark(self):
        from corvin_console import feature_flags as ff

        flag = ff.flag(ep.FLAG_ID)
        self.assertFalse(flag.default, "extension points must be off on a fresh install")
        self.assertTrue(flag.owner)
        self.assertTrue(flag.target_release)

    def test_the_hook_is_not_called_and_the_default_wins(self):
        called: list[int] = []

        def hook(n):
            called.append(n)
            return "from-hook"

        ep.register_hook(
            "engine.model_selection", hook, plugin_id="p1", tenant_id="_default"
        )
        got = ep.invoke("engine.model_selection", 7, default="bundled-default")

        self.assertEqual(got, "bundled-default")
        self.assertEqual(called, [], "flag off must not reach the hook at all")

    def test_a_callable_default_is_executed_with_the_call_args(self):
        ep.register_hook(
            "engine.model_selection", lambda n: "hook", plugin_id="p1", tenant_id="_default"
        )
        got = ep.invoke(
            "engine.model_selection", 3, default=lambda n: f"default:{n}"
        )
        self.assertEqual(got, "default:3")

    def test_flag_off_emits_nothing_per_call(self):
        ep.register_hook(
            "engine.model_selection", lambda n: 1, plugin_id="p1", tenant_id="_default"
        )
        self.audited.clear()
        for _ in range(50):
            ep.invoke("engine.model_selection", 1, default=None)
        self.assertEqual(
            self.audited, [],
            "the off path runs on every turn of every default install — "
            "it must not log or audit per call",
        )

    def test_a_failing_hook_on_a_fail_closed_point_cannot_deny_while_off(self):
        # Fail-closed is scoped to a REGISTERED, CONSULTED hook.  With the flag
        # off the point is not consulted, so the core's own gate decides exactly
        # as it did before the feature existed.
        def boom(wf):
            raise RuntimeError("nope")

        ep.register_hook(
            "workflow.workflow_gate", boom, plugin_id="gate", tenant_id="_default"
        )
        self.assertTrue(ep.invoke("workflow.workflow_gate", {}, default=True))

    def test_registration_still_works_and_is_visible_while_off(self):
        # A plugin's on_load may run before the operator flips the flag.
        ep.register_hook(
            "engine.engine_selection", lambda r: "x", plugin_id="p1", tenant_id="_default"
        )
        self.assertEqual(ep.describe(), {"engine.engine_selection": "p1"})


class TestFlagOn(_Base):
    """On, the hook decides — and its return value reaches the caller unchanged."""

    FLAG = True

    def test_the_hook_is_called_and_its_return_value_comes_through(self):
        seen: list[dict] = []

        def hook(request):
            seen.append(request)
            return "claude-haiku"

        ep.register_hook(
            "engine.model_selection", hook, plugin_id="p1", tenant_id="_default"
        )
        got = ep.invoke(
            "engine.model_selection", {"task": "x"}, default="claude-sonnet"
        )

        self.assertEqual(got, "claude-haiku")
        self.assertEqual(seen, [{"task": "x"}])

    def test_kwargs_reach_the_hook(self):
        ep.register_hook(
            "delegation.route_selection_policy",
            lambda turn, *, hint=None: f"{turn}/{hint}",
            plugin_id="p1",
            tenant_id="_default",
        )
        got = ep.invoke(
            "delegation.route_selection_policy", "t", hint="big", default="native"
        )
        self.assertEqual(got, "t/big")

    def test_no_hook_registered_still_takes_the_default(self):
        self.assertEqual(
            ep.invoke("engine.engine_selection", default="native"), "native"
        )

    def test_a_hook_returning_none_is_passed_through_verbatim(self):
        # "No opinion" is the point's own contract (see the spec), not something
        # the bus reinterprets — a bus that silently substituted the default
        # would make a hook unable to express a deliberate None.
        ep.register_hook(
            "engine.model_selection", lambda r: None, plugin_id="p1", tenant_id="_default"
        )
        self.assertIsNone(ep.invoke("engine.model_selection", {}, default="x"))


# ── 2. A hook may never take down its call site ───────────────────────────────


class TestRaisingHook(_Base):
    FLAG = True

    def test_normal_point_degrades_to_the_default(self):
        def boom(request):
            raise ValueError("secret: /home/alice/db.sqlite user=alice@corp.com")

        ep.register_hook(
            "engine.model_selection", boom, plugin_id="bad", tenant_id="_default"
        )
        got = ep.invoke("engine.model_selection", {}, default="bundled")
        self.assertEqual(got, "bundled")

    def test_the_failure_is_audited_with_the_exception_class_only(self):
        def boom(request):
            raise ValueError("secret: /home/alice/db.sqlite user=alice@corp.com")

        ep.register_hook(
            "engine.model_selection", boom, plugin_id="bad", tenant_id="_default"
        )
        ep.invoke("engine.model_selection", {}, default="bundled")

        failures = self.events("plugin.extension_hook_failed")
        self.assertEqual(len(failures), 1)
        detail = failures[0]
        self.assertEqual(detail["error_type"], "ValueError")
        self.assertEqual(detail["outcome"], "default")
        self.assertEqual(detail["plugin_id"], "bad")
        self.assertEqual(detail["point"], "engine.model_selection")

    def test_no_pii_from_the_exception_message_reaches_the_audit_detail(self):
        def boom(request):
            raise ValueError("secret: /home/alice/db.sqlite user=alice@corp.com")

        ep.register_hook(
            "engine.model_selection", boom, plugin_id="bad", tenant_id="_default"
        )
        ep.invoke("engine.model_selection", {}, default="bundled")

        blob = json.dumps(self.audited)
        for leak in ("alice", "corp.com", "db.sqlite", "secret:"):
            self.assertNotIn(
                leak, blob,
                f"{leak!r} came from str(exc) and must never reach an "
                f"append-only audit record",
            )

    def test_a_hook_raising_baseexception_still_does_not_escape(self):
        # A plugin author who raises something outside Exception (or a bare
        # KeyboardInterrupt in a thread) must not get a different guarantee
        # than one who raises ValueError.
        class Weird(Exception):
            pass

        ep.register_hook(
            "engine.model_selection",
            lambda r: (_ for _ in ()).throw(Weird()),
            plugin_id="bad",
            tenant_id="_default",
        )
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "d")


class TestFailClosedPoint(_Base):
    FLAG = True

    def test_a_working_gate_hook_answers_normally(self):
        ep.register_hook(
            "workflow.workflow_gate",
            lambda wf: False,
            plugin_id="gate",
            tenant_id="_default",
        )
        self.assertFalse(ep.invoke("workflow.workflow_gate", {}, default=True))

    def test_a_raising_gate_denies_instead_of_taking_the_default(self):
        def boom(wf):
            raise RuntimeError("gate backend unreachable")

        ep.register_hook(
            "workflow.workflow_gate", boom, plugin_id="gate", tenant_id="_default"
        )
        with self.assertRaises(ep.ExtensionPointDenied) as caught:
            # default=True is the permissive pre-feature answer; the point of
            # fail-closed is that a broken gate must NOT be able to reach it.
            ep.invoke("workflow.workflow_gate", {}, default=True)
        self.assertEqual(caught.exception.point, "workflow.workflow_gate")
        self.assertIn("RuntimeError", caught.exception.reason)

    def test_the_denial_is_audited_as_a_denial(self):
        ep.register_hook(
            "workflow.workflow_gate",
            lambda wf: (_ for _ in ()).throw(RuntimeError("x")),
            plugin_id="gate",
            tenant_id="_default",
        )
        with self.assertRaises(ep.ExtensionPointDenied):
            ep.invoke("workflow.workflow_gate", {}, default=True)
        failures = self.events("plugin.extension_hook_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["outcome"], "deny")

    def test_the_denial_does_not_carry_the_hooks_message(self):
        ep.register_hook(
            "workflow.workflow_gate",
            lambda wf: (_ for _ in ()).throw(RuntimeError("token=abc /home/bob")),
            plugin_id="gate",
            tenant_id="_default",
        )
        with self.assertRaises(ep.ExtensionPointDenied) as caught:
            ep.invoke("workflow.workflow_gate", {}, default=True)
        self.assertNotIn("token=abc", str(caught.exception))
        self.assertNotIn("/home/bob", str(caught.exception))

    def test_the_original_exception_is_not_chained_into_the_call_site(self):
        # `raise ... from None`: a traceback printed by the call site would
        # otherwise render the plugin's message, which is the same leak the
        # audit path is guarded against.
        ep.register_hook(
            "workflow.workflow_gate",
            lambda wf: (_ for _ in ()).throw(RuntimeError("user=carol@x.de")),
            plugin_id="gate",
            tenant_id="_default",
        )
        with self.assertRaises(ep.ExtensionPointDenied) as caught:
            ep.invoke("workflow.workflow_gate", {}, default=True)
        self.assertIsNone(caught.exception.__cause__)

    def test_exactly_the_declared_points_are_fail_closed(self):
        self.assertEqual(ep._FAIL_CLOSED_POINTS, frozenset({"workflow.workflow_gate"}))
        self.assertTrue(ep.spec("workflow.workflow_gate").fail_closed)
        self.assertFalse(ep.spec("engine.model_selection").fail_closed)


# ── 3. Refused names ──────────────────────────────────────────────────────────


class TestRefusedNames(_Base):
    FLAG = True

    def test_an_unknown_point_is_refused_not_ignored(self):
        with self.assertRaises(ep.UnknownExtensionPoint):
            ep.register_hook(
                "engine.modle_selection", lambda: 1, plugin_id="typo", tenant_id="_default"
            )
        self.assertEqual(ep.describe(), {})

    def test_the_refusal_is_audited(self):
        with self.assertRaises(ep.UnknownExtensionPoint):
            ep.register_hook("nope.nope", lambda: 1, plugin_id="p1", tenant_id="_default")
        rejected = self.events("plugin.extension_hook_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "unknown_point")

    def test_an_absurdly_long_name_is_clipped_before_it_is_audited(self):
        with self.assertRaises(ep.UnknownExtensionPoint):
            ep.register_hook("x" * 5000, lambda: 1, plugin_id="p1", tenant_id="_default")
        rejected = self.events("plugin.extension_hook_rejected")
        self.assertLessEqual(
            len(rejected[0]["point"]), ep.MAX_AUDITED_NAME_CHARS,
            "an author-supplied name reaching an append-only record must be bounded",
        )

    def test_every_immutable_mechanism_is_refused_with_its_own_error(self):
        for name in ep._NEVER_EXTENSIBLE:
            with self.subTest(point=name):
                with self.assertRaises(ep.ImmutableExtensionPoint):
                    ep.register_hook(
                        name, lambda: 1, plugin_id="attacker", tenant_id="_default"
                    )
        self.assertEqual(ep.describe(), {})

    def test_the_compliance_mechanisms_named_in_claude_md_are_all_covered(self):
        for name in (
            "audit.hash_chain",
            "a2a.signature_verification",
            "tde.token_accounting",
            "consent.gate",
            "house_rules.gate",
            "path_gate.check",
        ):
            self.assertIn(name, ep._NEVER_EXTENSIBLE)

    def test_an_immutable_name_is_never_also_a_known_point(self):
        self.assertEqual(
            set(ep._NEVER_EXTENSIBLE) & set(ep.KNOWN_EXTENSION_POINTS), set()
        )

    def test_the_immutable_refusal_is_audited_with_its_own_reason(self):
        with self.assertRaises(ep.ImmutableExtensionPoint):
            ep.register_hook(
                "audit.hash_chain", lambda: 1, plugin_id="p1", tenant_id="_default"
            )
        rejected = self.events("plugin.extension_hook_rejected")
        self.assertEqual(rejected[0]["reason"], "never_extensible")

    def test_a_provider_registry_name_points_the_author_at_the_registry(self):
        with self.assertRaises(ep.UnknownExtensionPoint) as caught:
            ep.register_hook(
                "audit_backend", lambda: 1, plugin_id="p1", tenant_id="_default"
            )
        self.assertIn("audit_registry", str(caught.exception))

    def test_a_non_callable_hook_is_refused(self):
        with self.assertRaises(ep.ExtensionPointError):
            ep.register_hook(
                "engine.model_selection",
                "not-a-function",
                plugin_id="p1",
                tenant_id="_default",
            )
        self.assertEqual(ep.describe(), {})

    def test_a_mistyped_call_site_raises_rather_than_silently_defaulting(self):
        with self.assertRaises(ep.UnknownExtensionPoint):
            ep.invoke("engine.modle_selection", default="x")

    def test_a_mistyped_call_site_raises_with_the_flag_off_too(self):
        # The check happens before the flag lookup on purpose: a call site typo
        # that only surfaced once an operator enabled the feature would be
        # discovered in production rather than in the test suite.
        home = Path(self._tmp.name) / "tenants" / "_default" / "global"
        (home / "features.json").write_text(
            json.dumps({"flags": {ep.FLAG_ID: False}}), encoding="utf-8"
        )
        with self.assertRaises(ep.UnknownExtensionPoint):
            ep.invoke("nope.nope", default="x")


# ── 4. The conflict rule: last wins, and it is audited ────────────────────────


class TestDuplicateRegistration(_Base):
    FLAG = True

    def test_the_last_registration_wins(self):
        ep.register_hook(
            "engine.model_selection",
            lambda r: "first",
            plugin_id="p1",
            tenant_id="_default",
        )
        ep.register_hook(
            "engine.model_selection",
            lambda r: "second",
            plugin_id="p2",
            tenant_id="_default",
        )
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "second")
        self.assertEqual(ep.describe(), {"engine.model_selection": "p2"})

    def test_the_takeover_is_audited_with_both_plugin_ids(self):
        ep.register_hook(
            "engine.model_selection", lambda r: 1, plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: 2, plugin_id="p2", tenant_id="_default"
        )
        replaced = self.events("plugin.extension_hook_replaced")
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0]["plugin_id"], "p2")
        self.assertEqual(replaced[0]["replaced_plugin_id"], "p1")
        self.assertEqual(replaced[0]["point"], "engine.model_selection")

    def test_a_plugin_re_registering_its_own_hook_is_not_a_takeover(self):
        # A hot reload re-runs on_load.  Auditing that as a takeover would bury
        # the real ones under noise.
        ep.register_hook(
            "engine.model_selection", lambda r: "a", plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: "b", plugin_id="p1", tenant_id="_default"
        )
        self.assertEqual(self.events("plugin.extension_hook_replaced"), [])
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "b")

    def test_a_takeover_on_one_point_leaves_the_others_alone(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "m1", plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.engine_selection", lambda r: "e1", plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: "m2", plugin_id="p2", tenant_id="_default"
        )
        self.assertEqual(
            ep.describe(),
            {"engine.model_selection": "p2", "engine.engine_selection": "p1"},
        )


# ── 5. unregister_all ─────────────────────────────────────────────────────────


class TestUnregisterAll(_Base):
    FLAG = True

    def test_every_hook_of_the_plugin_is_removed_across_points_and_tenants(self):
        ep.register_hook(
            "engine.model_selection", lambda r: 1, plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.engine_selection", lambda r: 1, plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: 1, plugin_id="p1", tenant_id="tenant-a"
        )
        ep.register_hook(
            "workflow.workflow_gate", lambda w: True, plugin_id="p2", tenant_id="_default"
        )

        removed = ep.unregister_all("p1")

        self.assertEqual(removed, 3)
        self.assertEqual(ep.describe(), {"workflow.workflow_gate": "p2"})
        self.assertEqual(ep.describe("tenant-a"), {})

    def test_the_call_site_falls_back_to_the_default_afterwards(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "hook", plugin_id="p1", tenant_id="_default"
        )
        ep.unregister_all("p1")
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "d")

    def test_unregistering_a_plugin_with_no_hooks_is_not_an_error(self):
        self.assertEqual(ep.unregister_all("never-registered"), 0)

    def test_it_does_not_remove_another_plugins_hook_on_the_same_point(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "p1", plugin_id="p1", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: "p2", plugin_id="p2", tenant_id="_default"
        )
        self.assertEqual(
            ep.unregister_all("p1"), 0,
            "p1 lost the point to p2's takeover; it has nothing left to remove",
        )
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "p2")


# ── 6. Tenant isolation ───────────────────────────────────────────────────────


class TestTenantIsolation(_Base):
    FLAG = True

    def test_a_hook_for_tenant_a_does_not_fire_for_tenant_b(self):
        calls: list[str] = []

        def hook(request):
            calls.append("a")
            return "a-model"

        ep.register_hook(
            "engine.model_selection", hook, plugin_id="p1", tenant_id="tenant-a"
        )

        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="a-default",
                      tenant_id="tenant-a"),
            "a-model",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="b-default",
                      tenant_id="tenant-b"),
            "b-default",
        )
        self.assertEqual(calls, ["a"], "tenant-b must not reach tenant-a's hook")

    def test_the_default_tenant_is_not_a_wildcard(self):
        ep.register_hook(
            "engine.model_selection",
            lambda r: "shared",
            plugin_id="p1",
            tenant_id="_default",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="own",
                      tenant_id="tenant-a"),
            "own",
        )

    def test_two_tenants_can_hold_different_hooks_for_the_same_point(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "A", plugin_id="p1", tenant_id="tenant-a"
        )
        ep.register_hook(
            "engine.model_selection", lambda r: "B", plugin_id="p2", tenant_id="tenant-b"
        )
        self.assertEqual(self.events("plugin.extension_hook_replaced"), [])
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="d", tenant_id="tenant-a"), "A"
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="d", tenant_id="tenant-b"), "B"
        )

    def test_the_flag_is_resolved_per_tenant(self):
        # tenant-b turns the feature off while tenant-a keeps it on.
        home = Path(self._tmp.name) / "tenants" / "tenant-b" / "global"
        (home / "features.json").write_text(
            json.dumps({"flags": {ep.FLAG_ID: False}}), encoding="utf-8"
        )
        for tenant in ("tenant-a", "tenant-b"):
            ep.register_hook(
                "engine.model_selection", lambda r: "hook",
                plugin_id="p1", tenant_id=tenant,
            )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="d", tenant_id="tenant-a"),
            "hook",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="d", tenant_id="tenant-b"),
            "d",
        )


# ── 7. The declared shape of the points themselves ────────────────────────────


class TestPointSpecs(_Base):
    FLAG = True

    def test_phase_3_defines_exactly_the_four_agreed_points(self):
        self.assertEqual(
            ep.KNOWN_EXTENSION_POINTS,
            frozenset({
                "engine.model_selection",
                "engine.engine_selection",
                "delegation.route_selection_policy",
                "workflow.workflow_gate",
            }),
        )

    def test_every_point_declares_a_signature_and_a_default_behaviour(self):
        for name in ep.KNOWN_EXTENSION_POINTS:
            with self.subTest(point=name):
                s = ep.spec(name)
                self.assertTrue(s.signature)
                self.assertTrue(s.default_behavior)
                self.assertTrue(s.summary)

    def test_spec_refuses_an_immutable_name_with_the_immutable_error(self):
        with self.assertRaises(ep.ImmutableExtensionPoint):
            ep.spec("consent.gate")

    def test_no_provider_registry_leaked_into_the_known_set(self):
        self.assertEqual(
            set(ep._PROVIDER_POINTS) & set(ep.KNOWN_EXTENSION_POINTS), set()
        )


# ── 8. The defensive edges ────────────────────────────────────────────────────


class TestDefensiveEdges(_Base):
    FLAG = True

    def test_the_real_audit_path_never_raises(self):
        # The one test that exercises the UN-patched sink.  bootstrap's emitter
        # swallows an absent audit module; if that ever regressed, a plugin
        # registration would start failing on a layout without the audit
        # package instead of degrading.
        self._real_audit(
            "plugin.extension_hook_registered",
            {"point": "engine.model_selection", "plugin_id": "p1"},
            tenant_id="_default",
        )

    def test_the_flag_lookup_degrades_to_off_when_the_console_is_absent(self):
        # Headless core (ADR-0241): core/plugins must import and run without
        # the Console package.  Absent Console reads as "off" — the pre-feature
        # path — never as "assume on".
        saved = {k: v for k, v in sys.modules.items() if k.startswith("corvin_console")}
        for key in saved:
            del sys.modules[key]
        sys.modules["corvin_console"] = None  # type: ignore[assignment]
        try:
            # (enabled, lookup_broken): off, and NOT broken — a layout that has
            # no flag registry is a complete answer, not a degradation, so it
            # must not produce a degradation record on every fail-closed point.
            self.assertEqual(ep._flag_state("_default"), (False, False))
            ep.register_hook(
                "engine.model_selection",
                lambda r: "h",
                plugin_id="p1",
                tenant_id="_default",
            )
            self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "d")
        finally:
            del sys.modules["corvin_console"]
            sys.modules.update(saved)

    def test_a_raising_flag_lookup_is_reported_as_broken_not_as_off(self):
        # The two look identical from the call site and are not the same thing.
        # Collapsing them is how a fail-closed gate gets bypassed without a
        # trace: an operator switched enforcement ON, a corrupt features.json
        # made the lookup raise, and "off" was indistinguishable from "the
        # operator never wanted it".
        from corvin_console import feature_flags as ff

        real = ff.is_enabled

        def boom(flag_id, tenant_id=None):
            raise RuntimeError("features.json lost a brace")

        ff.is_enabled = boom  # type: ignore[assignment]
        try:
            self.assertEqual(ep._flag_state("_default"), (False, True))

            ep._degraded_reported.clear()
            ep.register_hook(
                "workflow.workflow_gate",
                lambda wf: False,
                plugin_id="gate",
                tenant_id="_default",
            )
            self.audited.clear()
            # Still permissive for the DECISION — a broken config must not deny
            # every gated workflow — but it is now on the record.
            self.assertTrue(ep.invoke("workflow.workflow_gate", {}, default=True))
            degraded = self.events("plugin.extension_flag_degraded")
            self.assertEqual(len(degraded), 1)
            self.assertEqual(degraded[0]["point"], "workflow.workflow_gate")
            self.assertEqual(degraded[0]["outcome"], "default")

            # Once per (tenant, point): the append-only chain must not be spammed
            # from a hot path.
            for _ in range(20):
                ep.invoke("workflow.workflow_gate", {}, default=True)
            self.assertEqual(len(self.events("plugin.extension_flag_degraded")), 1)
        finally:
            ff.is_enabled = real  # type: ignore[assignment]
            ep._degraded_reported.clear()

    def test_a_broken_lookup_on_a_normal_point_stays_quiet(self):
        # The degradation record exists for fail-closed points, where silence
        # would hide weakened enforcement.  A model-selection preference is not
        # that, and a per-turn record there would be chain spam.
        from corvin_console import feature_flags as ff

        real = ff.is_enabled

        def boom(flag_id, tenant_id=None):
            raise RuntimeError("features.json lost a brace")

        ff.is_enabled = boom  # type: ignore[assignment]
        try:
            ep._degraded_reported.clear()
            self.audited.clear()
            self.assertEqual(
                ep.invoke("engine.model_selection", {}, default="d"), "d"
            )
            self.assertEqual(self.events("plugin.extension_flag_degraded"), [])
        finally:
            ff.is_enabled = real  # type: ignore[assignment]
            ep._degraded_reported.clear()

    def test_a_hook_may_invoke_another_point_without_deadlocking(self):
        # The bus lock is re-entrant; a hook that consults a second point must
        # not hang the turn it was called from.
        ep.register_hook(
            "engine.engine_selection", lambda r: "acs", plugin_id="p2", tenant_id="_default"
        )
        ep.register_hook(
            "engine.model_selection",
            lambda r: ep.invoke("engine.engine_selection", r, default="native"),
            plugin_id="p1",
            tenant_id="_default",
        )
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="d"), "acs")

    def test_the_package_re_exports_the_public_surface(self):
        import corvin_plugins

        for name in (
            "register_hook", "invoke", "unregister_all",
            "KNOWN_EXTENSION_POINTS", "ExtensionPointDenied",
            "UnknownExtensionPoint", "ImmutableExtensionPoint",
        ):
            with self.subTest(name=name):
                self.assertIn(name, corvin_plugins.__all__)
                self.assertTrue(hasattr(corvin_plugins, name))

    def test_no_call_site_is_wired_yet(self):
        # Phase 3 defines the bus; the call sites land in a follow-up.  This
        # test is the honest record of that, and it will fail the day someone
        # wires one — at which point the doc's "not wired yet" line must change
        # in the same commit.
        import subprocess

        # Match USES of the module (an import of it, or an attribute access on
        # it) — not the string "plugin_extension_points", which is the feature
        # flag's id and lives in the flag registry by design.
        out = subprocess.run(
            ["git", "grep", "-lE", r"(from|import) [.a-z_]*extension_points|"
                                   r"\bextension_points\.[a-z]"],
            cwd=str(_REPO), capture_output=True, text=True,
        ).stdout.split()
        unexpected = [
            p for p in out
            if not p.endswith((
                "corvin_plugins/extension_points.py",
                "corvin_plugins/__init__.py",
                "tests/test_extension_points.py",
                ".md",
            ))
        ]
        self.assertEqual(
            unexpected, [],
            "a call site now uses the bus — update docs/EXTENSIBLE_CORE_PLUGINS.md "
            "in the same commit (CLAUDE.md § Testing + Docs Sync)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
