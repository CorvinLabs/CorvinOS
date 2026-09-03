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
import threading
import time
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

    def test_a_hook_returning_none_abstains_and_the_default_runs(self):
        """ADR-0251 D3. This REPLACES the previous contract, deliberately.

        Until 2026-07-27 the bus passed `None` through verbatim and left the
        call site to interpret it, on the reasoning that a hook must be able to
        express a deliberate `None`. ADR-0251 D3 decided the opposite, and the
        point specs in `extension_points.py` had always agreed with the ADR
        rather than with the old test: `engine.model_selection`'s
        `default_behavior` says a `None` return is "treated exactly like no hook
        at all". The old test cited that spec and asserted its opposite.

        The decision is right on the merits too: `None` is not a meaningful
        answer on ANY of the four points — three return an id and the fourth
        returns a gate verdict — so pass-through bought no expressiveness and
        cost every call site its own `if result is None` branch, which is four
        places for the interpretation to drift.
        """
        ep.register_hook(
            "engine.model_selection", lambda r: None, plugin_id="p1", tenant_id="_default"
        )
        self.assertEqual(ep.invoke("engine.model_selection", {}, default="x"), "x")

    def test_none_abstains_on_a_fail_closed_point_too(self):
        """Abstention is not denial, even on the gate (ADR-0251 D3).

        The strict reading — `None` on a fail-closed point means deny — is
        defensible in isolation and wrong in practice: a gate that cares about
        some workflows and not others would have to return True for every run it
        has no opinion about, thereby overriding the core gate on all of them.
        """
        ep.register_hook(
            "workflow.workflow_gate", lambda w: None, plugin_id="p1", tenant_id="_default"
        )
        self.assertIs(
            ep.invoke("workflow.workflow_gate", {}, default=True), True
        )


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


# ── 6b. The tenant a plugin claims is checked against the one it was loaded for ─


class _Hooky:
    """A plugin whose ``on_load`` registers a hook, like a real one would."""

    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Hooky"

    def __init__(self, plugin_id: str, *, point: str = "engine.model_selection",
                 tenant_id: str | None = None):
        self.plugin_id = plugin_id
        self._point = point
        self._tenant_id = tenant_id
        self.registered_from_on_load = False
        self.on_load_error: BaseException | None = None

    def on_load(self, ctx):
        try:
            ep.register_hook(
                self._point,
                lambda *a, **k: "from-on-load",
                plugin_id=self.plugin_id,
                tenant_id=self._tenant_id or ctx.tenant_id,
            )
            self.registered_from_on_load = True
        except BaseException as exc:  # noqa: BLE001 — the test inspects it
            self.on_load_error = exc

    def on_unload(self):
        ep.unregister_all(self.plugin_id)

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus

        return HealthStatus(ok=True)


class TestTenantIsAuthorised(_Base):
    """`tenant_id` was made mandatory so nobody lands in `_default` by accident.

    Mandatory is not the same as checked, and unchecked it bought nothing: a
    plugin loaded for tenant A could still pass tenant B's id and — because last
    registration wins — TAKE OVER tenant B's fail-closed `workflow.workflow_gate`
    rather than collide with it.  The registry knows which tenant a plugin was
    loaded for, so the claim is now verified against it.
    """

    FLAG = True

    def setUp(self):
        super().setUp()
        import corvin_plugins.registry as _reg
        from corvin_plugins.registry import PluginRegistry

        self._reg_module = _reg
        self._orig_registry = _reg._registry
        _reg._registry = PluginRegistry()

    def tearDown(self):
        self._reg_module._registry = self._orig_registry
        super().tearDown()

    def _load(self, plugin, tenant_id: str):
        """Register a plugin through the REAL registry, for ``tenant_id``."""
        from corvin_plugins.protocol import PluginContext

        ctx = PluginContext(
            plugin_id=plugin.plugin_id,
            tenant_id=tenant_id,
            corvin_home=Path(self._tmp.name),
            config={},
            audit_emit=lambda event, details: None,
        )
        self._reg_module._registry.register(plugin, ctx)
        return ctx

    # — the reported defect —

    def test_a_plugin_may_not_take_over_another_tenants_fail_closed_gate(self):
        # The reviewer's reproduction, verbatim in intent: a plugin loaded for
        # tenant-a registers a deny-everything gate hook for tenant-b.
        victim_gate_ran: list[str] = []
        ep.register_hook(
            "workflow.workflow_gate",
            lambda wf: victim_gate_ran.append("b") or True,
            plugin_id="tenant-b-plugin",
            tenant_id="tenant-b",
        )
        self._load(_Hooky("tenant-a-plugin"), "tenant-a")

        with self.assertRaises(ep.CrossTenantHookRefused):
            ep.register_hook(
                "workflow.workflow_gate",
                lambda wf: False,
                plugin_id="tenant-a-plugin",
                tenant_id="tenant-b",
            )

        self.assertEqual(
            ep.describe("tenant-b"), {"workflow.workflow_gate": "tenant-b-plugin"},
            "tenant-b's gate must still belong to tenant-b",
        )
        self.assertTrue(
            ep.invoke("workflow.workflow_gate", {}, default=False, tenant_id="tenant-b")
        )
        self.assertEqual(victim_gate_ran, ["b"])

    def test_the_refusal_is_its_own_exception_type(self):
        self._load(_Hooky("a-plugin"), "tenant-a")
        with self.assertRaises(ep.CrossTenantHookRefused) as caught:
            ep.register_hook(
                "engine.model_selection", lambda r: 1,
                plugin_id="a-plugin", tenant_id="tenant-b",
            )
        exc = caught.exception
        self.assertIsInstance(exc, ep.ExtensionPointError)
        self.assertEqual(exc.plugin_id, "a-plugin")
        self.assertEqual(exc.requested_tenant_id, "tenant-b")
        self.assertEqual(exc.actual_tenant_id, "tenant-a")

    def test_a_normal_point_is_protected_too(self):
        # Not only the gate: routing another tenant's turns to an engine of your
        # choosing is not a lesser thing because it degrades gracefully.
        self._load(_Hooky("a-plugin"), "tenant-a")
        for point in sorted(ep.KNOWN_EXTENSION_POINTS):
            with self.subTest(point=point):
                with self.assertRaises(ep.CrossTenantHookRefused):
                    ep.register_hook(
                        point, lambda *a, **k: "mine",
                        plugin_id="a-plugin", tenant_id="tenant-b",
                    )
        self.assertEqual(ep.describe("tenant-b"), {})

    def test_the_rejection_is_audited_with_both_tenants(self):
        self._load(_Hooky("a-plugin"), "tenant-a")
        with self.assertRaises(ep.CrossTenantHookRefused):
            ep.register_hook(
                "workflow.workflow_gate", lambda w: False,
                plugin_id="a-plugin", tenant_id="tenant-b",
            )
        rejected = self.events("plugin.extension_hook_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "tenant_mismatch")
        self.assertEqual(rejected[0]["plugin_id"], "a-plugin")
        self.assertEqual(rejected[0]["tenant_id"], "tenant-a")
        self.assertEqual(rejected[0]["requested_tenant_id"], "tenant-b")
        self.assertEqual(rejected[0]["point"], "workflow.workflow_gate")

    def test_the_rejection_detail_carries_no_free_text_and_is_clipped(self):
        # Same rule as every other refusal path: the chain is append-only, so an
        # oversized or author-controlled string written there is permanent.
        long_tenant = "t" * 5000
        self._load(_Hooky("a-plugin"), "tenant-a")
        with self.assertRaises(ep.CrossTenantHookRefused):
            ep.register_hook(
                "engine.model_selection", lambda r: 1,
                plugin_id="a-plugin", tenant_id=long_tenant,
            )
        detail = self.events("plugin.extension_hook_rejected")[0]
        for key, value in detail.items():
            with self.subTest(key=key):
                self.assertLessEqual(len(str(value)), ep.MAX_AUDITED_NAME_CHARS)
        self.assertNotIn("error", detail)

    # — the allowed paths —

    def test_a_plugin_registering_for_its_own_tenant_is_allowed_and_marked(self):
        self._load(_Hooky("a-plugin"), "tenant-a")
        ep.register_hook(
            "engine.model_selection", lambda r: "mine",
            plugin_id="a-plugin", tenant_id="tenant-a",
        )
        self.assertEqual(ep.describe("tenant-a"), {"engine.model_selection": "a-plugin"})
        registered = self.events("plugin.extension_hook_registered")
        self.assertEqual(registered[-1]["tenant_check"], "attributed")

    def test_a_plugin_can_register_from_its_own_on_load(self):
        # The load-bearing precondition for the whole check: register() reserves
        # the slot and stores the context BEFORE calling on_load(), so a plugin
        # hooking from on_load is already findable and passes as VERIFIED.  If
        # that order ever flipped, every plugin would silently drop to
        # "unattributed" and the check would protect nothing.
        plugin = _Hooky("a-plugin")
        self._load(plugin, "tenant-a")
        self.assertIsNone(plugin.on_load_error)
        self.assertTrue(plugin.registered_from_on_load)
        self.assertEqual(ep.describe("tenant-a"), {"engine.model_selection": "a-plugin"})
        self.assertEqual(
            self.events("plugin.extension_hook_registered")[-1]["tenant_check"],
            "attributed",
        )

    def test_a_plugin_that_lies_from_its_own_on_load_fails_the_load(self):
        plugin = _Hooky("a-plugin", tenant_id="tenant-b")
        self._load(plugin, "tenant-a")
        self.assertIsInstance(plugin.on_load_error, ep.CrossTenantHookRefused)
        self.assertEqual(ep.describe("tenant-b"), {})

    def test_an_unregistered_caller_is_allowed_but_not_marked_verified(self):
        # The documented decision (see register_hook.__doc__): a caller the
        # registry does not know is not a loaded plugin claiming a foreign
        # tenant — it is bundled reference code, an embedding host, or a test.
        # Refusing it would break those and buy nothing, since the same line
        # that names a foreign tenant can name an unknown plugin_id.  It is
        # allowed, and the record says the claim was never verified.
        ep.register_hook(
            "engine.model_selection", lambda r: "x",
            plugin_id="not-in-the-registry", tenant_id="tenant-b",
        )
        self.assertEqual(
            ep.describe("tenant-b"), {"engine.model_selection": "not-in-the-registry"}
        )
        self.assertEqual(
            self.events("plugin.extension_hook_registered")[-1]["tenant_check"],
            "unattributed",
        )

    def test_an_unreachable_registry_does_not_break_a_registration(self):
        # Headless layouts import this module without a usable registry
        # (ADR-0241).  A lookup that cannot run must not turn every plugin load
        # into a crash — but it must not be mistaken for agreement either.
        import corvin_plugins.registry as _reg

        real = _reg.get_registry

        def boom():
            raise RuntimeError("no registry here")

        _reg.get_registry = boom  # type: ignore[assignment]
        try:
            ep.register_hook(
                "engine.model_selection", lambda r: "x",
                plugin_id="a-plugin", tenant_id="tenant-b",
            )
            self.assertEqual(
                self.events("plugin.extension_hook_registered")[-1]["tenant_check"],
                "unavailable",
            )
        finally:
            _reg.get_registry = real  # type: ignore[assignment]

    def test_the_takeover_rule_still_works_inside_one_tenant(self):
        # The check must not break ADR-0237's override: a user plugin beating a
        # bundled default is a takeover WITHIN a tenant and stays legal.
        self._load(_Hooky("bundled"), "tenant-a")
        self._load(_Hooky("override"), "tenant-a")
        self.assertEqual(ep.describe("tenant-a"), {"engine.model_selection": "override"})
        replaced = self.events("plugin.extension_hook_replaced")
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0]["replaced_plugin_id"], "bundled")
        self.assertEqual(replaced[0]["tenant_check"], "attributed")

    def test_unregister_all_still_reaches_across_tenants(self):
        # A plugin legitimately serving two tenants is loaded twice under two
        # ids; unloading either must still be able to clean up.
        self._load(_Hooky("a-plugin"), "tenant-a")
        self.assertEqual(ep.unregister_all("a-plugin"), 1)
        self.assertEqual(ep.describe("tenant-a"), {})


# ── 6c. The degradation memo is bounded and its check-and-set is atomic ────────


class TestDegradationMemo(_Base):
    FLAG = True

    def setUp(self):
        super().setUp()
        ep._degraded_reported.clear()

    def tearDown(self):
        ep._degraded_reported.clear()
        super().tearDown()

    def test_the_first_report_wins_and_the_rest_are_suppressed(self):
        self.assertTrue(ep._note_degraded("t", "workflow.workflow_gate"))
        for _ in range(5):
            self.assertFalse(ep._note_degraded("t", "workflow.workflow_gate"))

    def test_it_does_not_grow_without_bound(self):
        # Process-wide, keyed by tenant, in a process that runs for months.
        for i in range(ep.MAX_DEGRADED_REPORTED):
            ep._note_degraded(f"tenant-{i}", "workflow.workflow_gate")
        self.assertEqual(len(ep._degraded_reported), ep.MAX_DEGRADED_REPORTED)

        # At the cap the memo is dropped rather than the new key refused: a NEW
        # degradation must never be silenced, only possibly re-reported.
        self.assertTrue(ep._note_degraded("one-too-many", "workflow.workflow_gate"))
        self.assertEqual(len(ep._degraded_reported), 1)

    def test_concurrent_first_reports_produce_exactly_one_record(self):
        # `if key not in set: set.add(key)` is two steps; two turns for the same
        # tenant hitting the same broken features.json both saw "not yet
        # reported" and both appended to an append-only chain.
        results: list[bool] = []
        start = threading.Barrier(8)

        def run():
            start.wait(timeout=5)
            results.append(ep._note_degraded("t", "workflow.workflow_gate"))

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(results), 8)
        self.assertEqual(results.count(True), 1)


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
        # The flag registry is `corvin_core.feature_flags` (the Console package
        # ships it); "absent" is simulated on THAT import, not on a
        # `corvin_console` name the lookup never touches.
        saved = {k: v for k, v in sys.modules.items() if k.startswith("corvin_core")}
        for key in saved:
            del sys.modules[key]
        sys.modules["corvin_core"] = None  # type: ignore[assignment]
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
            del sys.modules["corvin_core"]
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

    def test_a_broken_console_is_reported_as_broken_not_as_absent(self):
        # The blanket `except Exception` around the IMPORT used to fold three
        # cases into two: Console absent (a complete answer), Console present
        # but its flag registry unimportable (a broken install), and a raising
        # lookup.  The middle one is where the operator most plausibly DID
        # enable the flag, and it read as a deliberate "off".
        saved = {k: v for k, v in sys.modules.items() if k.startswith("corvin_core")}
        broken_root = Path(self._tmp.name) / "broken-console"
        pkg = broken_root / "corvin_core"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "feature_flags.py").write_text(
            "import _a_dependency_this_install_is_missing\n", encoding="utf-8"
        )
        for key in saved:
            del sys.modules[key]
        sys.path.insert(0, str(broken_root))
        try:
            self.assertEqual(ep._flag_state("_default"), (False, True))

            # …and on a fail-closed point that now leaves a record, instead of
            # weakened enforcement that looks exactly like "never switched on".
            ep._degraded_reported.clear()
            self.audited.clear()
            self.assertTrue(ep.invoke("workflow.workflow_gate", {}, default=True))
            degraded = self.events("plugin.extension_flag_degraded")
            self.assertEqual(len(degraded), 1)
            self.assertEqual(degraded[0]["point"], "workflow.workflow_gate")
        finally:
            sys.path.remove(str(broken_root))
            for key in [k for k in sys.modules if k.startswith("corvin_core")]:
                del sys.modules[key]
            sys.modules.update(saved)
            ep._degraded_reported.clear()

    def test_an_absent_console_is_still_not_a_broken_one(self):
        # The other side of the same distinction — guarded here as well so a
        # future tightening of the import classification cannot start reporting
        # a headless layout (ADR-0241) as a degradation on every gate.
        saved = {k: v for k, v in sys.modules.items() if k.startswith("corvin_core")}
        for key in saved:
            del sys.modules[key]
        sys.modules["corvin_core"] = None  # type: ignore[assignment]
        try:
            self.assertEqual(ep._flag_state("_default"), (False, False))
        finally:
            del sys.modules["corvin_core"]
            sys.modules.update(saved)

    # `test_no_call_site_is_wired_yet` stood here until 2026-07-27. It asserted
    # that NOTHING used the bus, and it named its own successor: "When the call
    # sites land, THIS test goes away and that one carries the guarantee at
    # finer grain." ADR-0251's first call site landed, so it did.
    #
    # `test_extension_point_call_sites.py` now carries it per point and in BOTH
    # directions: a point gaining a caller must move from `_UNWIRED_POINTS` to
    # `_WIRED_POINTS`, and a wired point LOSING its caller fails too. The
    # repo-wide version could only ever have said "none yet", which stops being
    # a useful claim the moment the answer is "some".


# ── 7. Return-value semantics (ADR-0251 D3) ───────────────────────────────────


class TestReturnTypeIsChecked(_Base):
    """A wrong TYPE is a broken hook, not an abstention.

    From the call site's view "the gate returned a dict" and "the gate crashed"
    are the same event: no decision was produced. So the two are handled
    identically — the default on an ordinary point, a denial on a fail-closed
    one.
    """

    FLAG = True

    def test_wrong_type_on_an_ordinary_point_takes_the_default(self):
        ep.register_hook(
            "engine.engine_selection", lambda r: {"engine": "acs"},
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(
            ep.invoke("engine.engine_selection", {}, default="native"), "native"
        )

    def test_wrong_type_on_the_gate_denies(self):
        ep.register_hook(
            "workflow.workflow_gate", lambda w: "yes",
            plugin_id="p1", tenant_id="_default",
        )
        with self.assertRaises(ep.ExtensionPointDenied):
            ep.invoke("workflow.workflow_gate", {}, default=True)

    def test_an_int_is_not_a_bool_on_the_gate(self):
        # Guards the one place a plain isinstance check could go wrong if a
        # point ever declared `int`: bool IS a subclass of int, but 1 is not an
        # instance of bool, so `1` must not sneak past the gate's contract.
        ep.register_hook(
            "workflow.workflow_gate", lambda w: 1,
            plugin_id="p1", tenant_id="_default",
        )
        with self.assertRaises(ep.ExtensionPointDenied):
            ep.invoke("workflow.workflow_gate", {}, default=True)

    def test_a_bool_is_not_a_str_on_a_routing_point(self):
        ep.register_hook(
            "engine.model_selection", lambda r: True,
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="haiku"), "haiku"
        )

    def test_the_refusal_is_audited_without_the_value(self):
        ep.register_hook(
            "engine.model_selection", lambda r: {"secret": "sk-live-abc"},
            plugin_id="p1", tenant_id="_default",
        )
        ep.invoke("engine.model_selection", {}, default="haiku")
        events = [e for e in self.audited if e[0] == "plugin.extension_return_invalid"]
        self.assertEqual(len(events), 1, self.audited)
        details = events[0][1]
        self.assertEqual(details["returned_type"], "dict")
        self.assertEqual(details["expected_type"], "str")
        self.assertEqual(details["plugin_id"], "p1")
        # The VALUE never reaches the chain. A misbehaving hook's return could
        # be anything at all, including a credential or a prompt fragment, and
        # an audit record cannot be taken back.
        self.assertNotIn("sk-live-abc", json.dumps(details))

    def test_a_correct_type_is_returned_unchanged(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "sonnet",
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="haiku"), "sonnet"
        )

    def test_the_gate_accepts_a_real_bool(self):
        ep.register_hook(
            "workflow.workflow_gate", lambda w: False,
            plugin_id="p1", tenant_id="_default",
        )
        self.assertIs(
            ep.invoke("workflow.workflow_gate", {}, default=True), False
        )


# ── 8. Latency is measured and audited, never enforced (ADR-0251 D5) ──────────


class TestLatencyIsMeasuredNotEnforced(_Base):
    FLAG = True

    def setUp(self):
        super().setUp()
        ep._slow_reported.clear()

    def tearDown(self):
        ep._slow_reported.clear()
        super().tearDown()

    def test_a_slow_hook_still_returns_its_answer(self):
        """The budget is advisory. This is the honest half of D5.

        There is no timeout enforceable against arbitrary synchronous Python
        without a thread or a subprocess. A test asserting that a slow hook is
        CUT OFF would be asserting a guarantee the code does not provide —
        ADR-0249's attribution-not-containment posture, one layer down.
        """
        budget = ep._BY_NAME["engine.model_selection"].latency_budget_ms

        def _slow(_req):
            time.sleep(budget / 1000.0 * 3)
            return "sonnet"

        ep.register_hook(
            "engine.model_selection", _slow, plugin_id="slowpoke",
            tenant_id="_default",
        )
        self.assertEqual(
            ep.invoke("engine.model_selection", {}, default="haiku"), "sonnet"
        )

    def test_an_overrun_is_audited_with_the_plugin_and_the_elapsed_time(self):
        budget = ep._BY_NAME["engine.model_selection"].latency_budget_ms

        def _slow(_req):
            time.sleep(budget / 1000.0 * 3)
            return "sonnet"

        ep.register_hook(
            "engine.model_selection", _slow, plugin_id="slowpoke",
            tenant_id="_default",
        )
        ep.invoke("engine.model_selection", {}, default="haiku")
        events = [e for e in self.audited if e[0] == "plugin.extension_hook_slow"]
        self.assertEqual(len(events), 1, self.audited)
        self.assertEqual(events[0][1]["plugin_id"], "slowpoke")
        self.assertGreater(events[0][1]["elapsed_ms"], budget)

    def test_a_fast_hook_is_not_audited(self):
        ep.register_hook(
            "engine.model_selection", lambda r: "sonnet", plugin_id="quick",
            tenant_id="_default",
        )
        ep.invoke("engine.model_selection", {}, default="haiku")
        self.assertEqual(
            [e for e in self.audited if e[0] == "plugin.extension_hook_slow"], []
        )

    def test_a_slow_hook_on_a_hot_path_is_recorded_once(self):
        """The chain is append-only and a slow hook sits on the turn path.

        Without the memo, one misbehaving plugin appends one unremovable record
        per turn — the same chain-spam failure the flag-degradation notice
        already guards against.
        """
        budget = ep._BY_NAME["engine.model_selection"].latency_budget_ms

        def _slow(_req):
            time.sleep(budget / 1000.0 * 3)
            return "sonnet"

        ep.register_hook(
            "engine.model_selection", _slow, plugin_id="slowpoke",
            tenant_id="_default",
        )
        for _ in range(3):
            ep.invoke("engine.model_selection", {}, default="haiku")
        self.assertEqual(
            len([e for e in self.audited if e[0] == "plugin.extension_hook_slow"]), 1
        )

    def test_two_slow_plugins_on_one_point_stay_distinguishable(self):
        # The memo is keyed by (tenant, point, plugin), not (tenant, point):
        # otherwise the second plugin's overrun would be suppressed by the
        # first's record and the operator would see only one culprit.
        self.assertTrue(ep._note_slow("t", "engine.model_selection", "a"))
        self.assertTrue(ep._note_slow("t", "engine.model_selection", "b"))
        self.assertFalse(ep._note_slow("t", "engine.model_selection", "a"))

    def test_the_slow_memo_does_not_grow_without_bound(self):
        for i in range(ep.MAX_SLOW_REPORTED):
            ep._note_slow(f"tenant-{i}", "engine.model_selection", "p")
        self.assertEqual(len(ep._slow_reported), ep.MAX_SLOW_REPORTED)
        self.assertTrue(ep._note_slow("one-too-many", "engine.model_selection", "p"))
        self.assertEqual(len(ep._slow_reported), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
