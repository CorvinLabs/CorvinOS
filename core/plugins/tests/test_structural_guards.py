"""The three guards that were rebuilt structurally after round 3.

Rounds 1–3 of the adversarial pass kept finding the same shape: a guard placed
on the path the previous reviewer took, with the effect still reachable one path
over. Fix B gated `health_check_all`, not the breaker. Fix C gated `disable`,
not `enable`. Fix D gated the one registration window where the plugin is
resolvable, not the two beside it.

So the guards moved to where the decision is made ONCE:

* **Containment** is decided in `circuit_breaker.get_breaker()`, so every caller
  — health aggregate, audit fan-out, healing — inherits it.
* **Provider-slot ownership** is recorded when the slot is taken
  (`loading.current()`), so release works by identity instead of by matching the
  object or guessing from `plugin_type`.
* **Hook ownership** is verified when the plugin loads, so a claim staked before
  it was resolvable cannot outlive the load.

These tests attack the guards the way round 3 did, not the way the fix was
written.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import corvin_plugins.circuit_breaker as breakers  # noqa: E402
import corvin_plugins.extension_points as ep  # noqa: E402
from corvin_plugins.bootstrap import build_context  # noqa: E402
from corvin_plugins.manifest import BootLayer  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


def _ctx(plugin_id: str, tenant_id: str = "_default") -> PluginContext:
    return build_context(
        plugin_id=plugin_id, tenant_id=tenant_id, corvin_home=Path("/tmp")
    )


class _Plug:
    plugin_type = "compute_engine"
    version = "1.0.0"
    display_name = "P"

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.reg = get_registry()
        # Clear the same-epoch unregistration tracking. This prevents thread-escape
        # detection (ADR-0233 D5) from triggering on re-registrations of the same
        # plugin name across different tests. We don't advance the epoch because that
        # would cause registered plugins to be downgraded on re-registration in a
        # later epoch (a cross-epoch downgrade, also a thread-escape mitigation).
        self.reg._unregistered_this_epoch.clear()
        ep.clear_all()

    def tearDown(self):
        for pid in list(self.reg.discover()):
            breakers.forget(pid)
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass
        ep.clear_all()


# ── 1. Containment is decided in one place ────────────────────────────────────


class TestContainmentIsCentral(_Base):
    def test_the_breaker_itself_refuses_to_open_for_compliance(self):
        # Round 3 broke the previous fix through audit_backend._deliver_inner
        # and healing._circuit_break, both of which take the same breaker and
        # were never gated. Attack the breaker directly: if IT holds, every
        # caller holds.
        self.reg.register(_Plug("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE)
        breaker = breakers.get_breaker("audit")
        for _ in range(10):
            breaker.record_failure(RuntimeError("siem down"))

        self.assertNotEqual(breaker.stats().to_dict().get("state"), "open")
        breaker.guard()  # must not raise

    def test_failures_are_still_counted_and_visible(self):
        # Not containing must not mean not noticing.
        self.reg.register(_Plug("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE)
        breaker = breakers.get_breaker("audit")
        for _ in range(3):
            breaker.record_failure(RuntimeError("siem down"))
        stats = breaker.stats().to_dict()
        self.assertGreaterEqual(stats.get("consecutive_failures", 0), 3)

    def test_an_ordinary_plugin_is_still_contained(self):
        # Counter-test: a fix that stopped opening breakers everywhere would
        # pass the first test and remove containment from the whole platform.
        self.reg.register(_Plug("ordinary"), _ctx("ordinary"))
        breaker = breakers.get_breaker("ordinary")
        for _ in range(10):
            breaker.record_failure(RuntimeError("boom"))
        self.assertEqual(breaker.stats().to_dict().get("state"), "open")
        with self.assertRaises(breakers.CircuitOpen):
            breaker.guard()


# ── 2. Provider slots are released by owner, not by shape ─────────────────────


class TestSlotReleaseIsByOwner(_Base):
    def test_a_delegate_object_in_the_slot_is_still_released(self):
        """Round 3's regression: the plugin installed a helper, not itself.

        Matching the slot's contents against the plugin object left the helper
        installed after the operator disabled the plugin — and it went on
        receiving a copy of every audit event of every tenant.
        """
        from corvin_plugins.providers import audit_backend

        class _Sink:
            def fanout(self, *a, **k):
                pass

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        class _WithHelper(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(_Sink())

        self.reg.register(_WithHelper("helper-plugin"), _ctx("helper-plugin"))
        self.assertIsNotNone(audit_backend.get_active())

        self.reg.unregister("helper-plugin")
        self.assertIsNone(
            audit_backend.get_active(),
            "the slot still holds a helper installed by a plugin that is gone",
        )

    def test_a_plugin_type_without_a_provider_module_still_releases(self):
        """Round 3's second hole: the release picked the module from plugin_type.

        build_context hands every registry to every plugin, so a bridge_channel
        plugin can take the audit slot — and then nothing could release it,
        because bridge_channel names no provider module.
        """
        from corvin_plugins.providers import audit_backend

        class _Bridge(_Plug):
            plugin_type = "bridge_channel"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(self)

            def fanout(self, *a, **k):
                pass

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        self.reg.register(_Bridge("odd-bridge"), _ctx("odd-bridge"))
        self.assertIsNotNone(audit_backend.get_active())

        self.reg.unregister("odd-bridge")
        self.assertIsNone(
            audit_backend.get_active(),
            "a plugin whose type names no provider module kept the slot forever",
        )

    def test_a_failed_load_does_not_leave_a_slot_behind(self):
        from corvin_plugins.providers import audit_backend

        class _Broken(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(self)
                raise RuntimeError("half loaded")

            def fanout(self, *a, **k):
                pass

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        with self.assertRaises(RuntimeError):
            self.reg.register(_Broken("half"), _ctx("half"))
        self.assertIsNone(audit_backend.get_active())


# ── 3. A hook claimed before the load does not survive it ─────────────────────


class TestHookOwnershipIsVerifiedOnLoad(_Base):
    def test_a_claim_staked_from_init_is_revoked_when_the_plugin_loads(self):
        """Round 3's CRITICAL: __init__ runs before register().

        At construction time the plugin is in neither the load context nor the
        registry, so the up-front check cannot resolve it and takes the
        "unregistered, therefore allowed" path — which is where a plugin author
        naturally puts setup, and where a hostile one would claim another
        tenant's fail-closed gate.
        """
        class _Greedy(_Plug):
            def __init__(self, plugin_id: str) -> None:
                super().__init__(plugin_id)
                ep.register_hook(
                    "workflow.workflow_gate",
                    lambda workflow: False,
                    plugin_id=plugin_id,
                    tenant_id="victim-tenant",
                )

        plugin = _Greedy("greedy")
        # The claim lands — nothing could check it yet.
        self.assertIn("workflow.workflow_gate", ep.describe("victim-tenant"))

        self.reg.register(plugin, _ctx("greedy", tenant_id="owner-tenant"))

        self.assertEqual(
            ep.describe("victim-tenant"), {},
            "a hook claimed before the load survived it — registration, not "
            "loading, got the last word",
        )

    def test_a_hook_for_its_own_tenant_survives_the_load(self):
        # Counter-test: revoking everything would pass the test above and make
        # the bus useless.
        class _Honest(_Plug):
            def __init__(self, plugin_id: str) -> None:
                super().__init__(plugin_id)
                ep.register_hook(
                    "engine.model_selection",
                    lambda request: "haiku",
                    plugin_id=plugin_id,
                    tenant_id="owner-tenant",
                )

        self.reg.register(_Honest("honest"), _ctx("honest", tenant_id="owner-tenant"))
        self.assertEqual(
            ep.describe("owner-tenant"), {"engine.model_selection": "honest"}
        )

    def test_registering_during_on_load_is_verified_not_merely_allowed(self):
        # The load context now answers, so a hook registered in on_load for a
        # foreign tenant is refused outright rather than recorded as
        # "unregistered".
        refused: list[str] = []

        class _DuringLoad(_Plug):
            def on_load(self, ctx):
                try:
                    ep.register_hook(
                        "workflow.workflow_gate",
                        lambda workflow: False,
                        plugin_id=self.plugin_id,
                        tenant_id="somebody-else",
                    )
                except ep.CrossTenantHookRefused as exc:
                    refused.append(type(exc).__name__)

        self.reg.register(_DuringLoad("during"), _ctx("during", tenant_id="mine"))
        self.assertEqual(refused, ["CrossTenantHookRefused"])
        self.assertEqual(ep.describe("somebody-else"), {})


class TestIdentityIsDerivedNotAccepted(_Base):
    """Round 4: every guard asked a question whose answer the caller supplied.

    `register_hook(plugin_id=...)` is a parameter. Checking the tenant of
    whatever plugin that string names answers the wrong question — registering
    under a VICTIM's id and the victim's tenant passed the tenant check, and
    then survived both cleanup paths, because `verify_owner` and
    `unregister_all` filter on the same self-declared field.
    """

    def test_a_loading_plugin_cannot_register_under_another_plugins_id(self):
        refused: list[str] = []

        class _Victim(_Plug):
            pass

        class _Impostor(_Plug):
            def on_load(self, ctx):
                try:
                    ep.register_hook(
                        "workflow.workflow_gate",
                        lambda workflow: False,
                        plugin_id="victim",          # not mine
                        tenant_id="victim-tenant",   # and matching, so the
                                                     # tenant check alone passes
                    )
                except ep.CrossTenantHookRefused as exc:
                    refused.append(type(exc).__name__)

        self.reg.register(
            _Victim("victim"), _ctx("victim", tenant_id="victim-tenant")
        )
        self.reg.register(
            _Impostor("impostor"), _ctx("impostor", tenant_id="other-tenant")
        )

        self.assertEqual(refused, ["CrossTenantHookRefused"])
        self.assertEqual(
            ep.describe("victim-tenant"), {},
            "a plugin registered a hook under another plugin's identity, which "
            "then survives both cleanup paths",
        )

    def test_a_plugin_may_still_register_under_its_own_id(self):
        # Counter-test: rejecting every id would make the bus unusable.
        class _Honest(_Plug):
            def on_load(self, ctx):
                ep.register_hook(
                    "engine.model_selection",
                    lambda request: "haiku",
                    plugin_id=self.plugin_id,
                    tenant_id=ctx.tenant_id,
                )

        self.reg.register(_Honest("honest"), _ctx("honest", tenant_id="mine"))
        self.assertEqual(ep.describe("mine"), {"engine.model_selection": "honest"})


class TestUnloadRemovesHooks(_Base):
    """`unregister_all` had no production caller for three rounds."""

    def test_unregistering_a_plugin_drops_its_hooks(self):
        class _Hooked(_Plug):
            def on_load(self, ctx):
                ep.register_hook(
                    "workflow.workflow_gate",
                    lambda workflow: False,
                    plugin_id=self.plugin_id,
                    tenant_id=ctx.tenant_id,
                )

        self.reg.register(_Hooked("hooked"), _ctx("hooked", tenant_id="t"))
        self.assertIn("workflow.workflow_gate", ep.describe("t"))

        self.reg.unregister("hooked")
        self.assertEqual(
            ep.describe("t"), {},
            "a hook on the fail-closed gate outlived the plugin that owns it",
        )


class TestTheOwnerRecordItself(_Base):
    """Round 5 found these four lines deletable without any test noticing.

    They are the two halves of "a plugin has ONE identity": the owner is only
    ever written by a plugin that is loading, and the breaker is keyed on that
    owner rather than on whatever object sits in the slot. Both were the named
    subject of the commit that introduced them, and neither was covered.
    """

    def test_set_active_outside_a_load_does_not_erase_the_owner(self):
        # Writing None here did not merely leave the new occupant unowned — it
        # wiped the previous legitimate owner, after which release_owned_by
        # matched nobody and the slot could never be released again.
        from corvin_plugins.providers import audit_backend

        class _Sink:
            def fanout(self, *a, **k):
                pass

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        class _Owner(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(_Sink())

        self.reg.register(_Owner("legit"), _ctx("legit"))
        self.assertEqual(audit_backend.owner_plugin_id(), "legit")

        # Anything at all calling set_active from outside a load.
        audit_backend.set_active(_Sink())
        self.assertEqual(
            audit_backend.owner_plugin_id(), "legit",
            "a set_active outside a load erased the recorded owner, so nothing "
            "could release the slot afterwards",
        )

        self.reg.unregister("legit")
        self.assertIsNone(audit_backend.get_active())

    def test_the_breaker_is_keyed_on_the_owner_not_on_the_object(self):
        # A plugin that installs a helper has no plugin_id on that helper, so
        # keying on the object produced `anonymous:<Class>` — an id the registry
        # has never heard of, which means get_breaker() cannot see the boot
        # layer and the compliance exemption silently does not apply.
        import corvin_plugins.circuit_breaker as cb
        from corvin_plugins.providers import audit_backend

        class _Sink:
            def fanout(self, *a, **k):
                raise RuntimeError("siem down")

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        class _Compliance(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(_Sink())

        self.reg.register(
            _Compliance("core-audit"), _ctx("core-audit"),
            boot_layer=BootLayer.COMPLIANCE,
        )
        audit_backend.fanout("x", {}, tenant_id="_default")
        audit_backend.drain_now(timeout=2.0)

        keys = set(cb.snapshot())
        self.assertNotIn(
            "anonymous:_Sink", keys,
            "the fan-out keyed its breaker on the helper object, so the "
            "compliance plugin had two identities and only one of them was "
            "exempt from containment",
        )
        if "core-audit" in keys:
            self.assertFalse(
                keys and cb.get_breaker("core-audit").stats().to_dict()["containable"],
                "the owner's breaker must carry the compliance exemption",
            )


class TestTheHonestAsyncPluginIsNotPunished(_Base):
    """Connecting asynchronously is the normal shape, not an attack.

    `on_load` starts a thread that installs the backend once the connection is
    up. ContextVars do not cross into that thread, so the slot ends up with no
    recorded owner — and `release_owned_by` then found nothing to release. The
    slot kept the object of a plugin whose `on_unload` had already run, which
    for a recall backend means every later turn writes into a closed handle.
    """

    def test_a_slot_taken_from_a_worker_thread_is_still_released(self):
        import threading

        from corvin_plugins.providers import audit_backend

        installed = threading.Event()

        class _AsyncConnect(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                def _connect():
                    ctx.audit_registry.set_active(self)
                    installed.set()

                threading.Thread(target=_connect).start()
                installed.wait(timeout=2)

            def fanout(self, *a, **k):
                pass

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        plugin = _AsyncConnect("async-sink")
        self.reg.register(plugin, _ctx("async-sink"))
        self.assertIs(audit_backend.get_active(), plugin)

        self.reg.unregister("async-sink")
        self.assertIsNone(
            audit_backend.get_active(),
            "the slot still holds the object of an unloaded plugin — the honest "
            "async-connect pattern loses its slot forever",
        )


class TestTheCombinationOfBothKnownShapes(_Base):
    """Round 6: each fix covered one shape; their intersection covered neither.

    A plugin that installs a HELPER object (the round-3 shape) FROM A WORKER
    THREAD (the round-5 shape) has no owner — ContextVars do not cross threads —
    and no object identity, because the helper is not the plugin. Both release
    paths missed it, so after the operator's `disable()` the helper went on
    receiving every audit event of every tenant.
    """

    def test_a_helper_installed_from_a_thread_is_released_on_disable(self):
        import threading

        from corvin_plugins.providers import audit_backend

        received: list[tuple[str, str]] = []
        installed = threading.Event()

        class _Sink:
            def fanout(self, event_type, details, *, severity="INFO",
                       tenant_id="_default"):
                received.append((event_type, tenant_id))

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, *a, **k):
                return {}

        class _AsyncHelper(_Plug):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                def _connect():
                    ctx.audit_registry.set_active(_Sink())
                    installed.set()

                threading.Thread(target=_connect).start()
                installed.wait(timeout=2)

        self.reg.register(_AsyncHelper("async-helper"), _ctx("async-helper"))
        self.assertIsNotNone(audit_backend.get_active())

        # The operator disables it — the path that answers 403 for compliance
        # and 200 for everything else.
        self.reg.disable("async-helper")

        audit_backend.fanout("user.login", {}, tenant_id="tenant-b")
        audit_backend.drain_now(timeout=1.0)

        self.assertIsNone(
            audit_backend.get_active(),
            "a disabled plugin's helper still holds the audit slot",
        )
        self.assertEqual(
            received, [],
            f"a disabled plugin kept receiving other tenants' audit events: "
            f"{received}",
        )


class TestHealthChecksCannotWedgeTheProcess(_Base):
    """`health_check_all` runs on every GET /api/admin/* — it needs a deadline.

    The breaker counts raises and cooperative ok=False. A check that simply
    never returns is neither, so nothing capped it: one wedged plugin held the
    whole admin surface, with no recovery path and no signal. On the compliance
    boot layer it was worse, because containment is deliberately off there.
    """

    def test_a_wedged_health_check_does_not_hold_the_aggregate(self):
        import time

        class _Wedged(_Plug):
            def health_check(self):
                time.sleep(30)
                return HealthStatus(ok=True)

        self.reg.register(_Wedged("wedged"), _ctx("wedged"))

        started = time.monotonic()
        results = self.reg.health_check_all()
        elapsed = time.monotonic() - started

        self.assertLess(
            elapsed, 10,
            f"the aggregate waited {elapsed:.1f}s on one plugin — a wedged "
            f"health_check holds every admin request",
        )
        self.assertFalse(results["wedged"].ok)

    def test_the_deadline_also_applies_on_the_compliance_layer(self):
        import time

        class _Wedged(_Plug):
            def health_check(self):
                time.sleep(30)
                return HealthStatus(ok=True)

        self.reg.register(
            _Wedged("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE
        )
        started = time.monotonic()
        self.reg.health_check_all()
        self.assertLess(time.monotonic() - started, 10)


class TestCleanupAfterAFailedLoad(_Base):
    """A half-loaded plugin must not leave anything behind.

    Nothing will come back for it: it is not registered, so no unregister ever
    runs. Releasing only the provider slot left a hook on the fail-closed gate —
    with the callable of a half-initialised object — and a breaker carrying its
    failures.
    """

    def test_a_failed_load_leaves_no_hook_behind(self):
        class _Broken(_Plug):
            def on_load(self, ctx):
                ep.register_hook(
                    "workflow.workflow_gate",
                    lambda workflow: False,
                    plugin_id=self.plugin_id,
                    tenant_id=ctx.tenant_id,
                )
                raise RuntimeError("half loaded")

        with self.assertRaises(RuntimeError):
            self.reg.register(_Broken("halfhook"), _ctx("halfhook", tenant_id="t"))

        self.assertEqual(
            ep.describe("t"), {},
            "a hook registered by a plugin that failed to load is still armed",
        )

    def test_a_failed_load_leaves_no_breaker_behind(self):
        # The third line of the rollback block. Its two siblings
        # (_detach_provider_slot, _revoke_hooks) were tested; this one was not.
        # A plugin whose on_load records failures on its own breaker and then
        # raises would hand the open breaker to the next attempt.
        class _Broken(_Plug):
            def on_load(self, ctx):
                breakers.get_breaker(self.plugin_id).record_failure(
                    RuntimeError("backend down")
                )
                raise RuntimeError("nope")

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                self.reg.register(_Broken("flaky"), _ctx("flaky"))

        stats = breakers.get_breaker("flaky").stats().to_dict()
        self.assertNotEqual(
            stats.get("state"), "open",
            "three failed registrations left an open breaker for a plugin that "
            "never loaded — the next attempt inherits it",
        )


class TestTheWindowAfterUnloadIsClosed(_Base):
    """An id stays bound to its tenant even once the object is gone.

    After `unregister` the plugin is in neither the load context nor the
    registry, so the tenant check had nothing to compare against and took the
    "unregistered, therefore allowed" path — a closure, timer or thread that
    outlived the plugin could claim any tenant's fail-closed gate, and no
    cleanup path would ever revoke it.
    """

    def test_a_late_registration_cannot_claim_a_foreign_tenant(self):
        self.reg.register(_Plug("late"), _ctx("late", tenant_id="own-tenant"))
        self.reg.unregister("late")

        with self.assertRaises(ep.CrossTenantHookRefused):
            ep.register_hook(
                "workflow.workflow_gate",
                lambda workflow: False,
                plugin_id="late",
                tenant_id="victim-tenant",
            )
        self.assertEqual(ep.describe("victim-tenant"), {})

    def test_a_late_registration_for_its_own_tenant_is_still_allowed(self):
        # Counter-test: binding the id must not make it unusable afterwards.
        self.reg.register(_Plug("late2"), _ctx("late2", tenant_id="own-tenant"))
        self.reg.unregister("late2")

        ep.register_hook(
            "engine.model_selection",
            lambda request: "haiku",
            plugin_id="late2",
            tenant_id="own-tenant",
        )
        self.assertEqual(
            ep.describe("own-tenant"), {"engine.model_selection": "late2"}
        )


class TestNoSelfPrivilegingFromInsideALoad(_Base):
    """`register(..., boot_layer=)` is importable, and on_load is arbitrary code.

    The object-attribute cap never covered the ARGUMENT. An installed plugin
    could import the module-level `register` and put a second object on the
    compliance boot layer from inside its own load — no PluginRecord, no
    consent gate, no L34/L35 declarations, and not disableable afterwards.
    """

    def test_a_plugin_cannot_register_a_privileged_object_during_its_own_load(self):
        from corvin_plugins.registry import register as module_register

        class _Shadow(_Plug):
            pass

        class _Sneaky(_Plug):
            def on_load(self, ctx):
                module_register(
                    _Shadow("shadow"), _ctx("shadow"),
                    boot_layer=BootLayer.COMPLIANCE,
                )

        self.reg.register(_Sneaky("sneaky"), _ctx("sneaky"))
        self.assertIs(
            self.reg.boot_layer_of("shadow"), BootLayer.INSTALLED,
            "a plugin promoted a second object to a privileged boot layer from "
            "inside its own on_load",
        )
        self.assertTrue(self.reg.can_disable("shadow"))

    def test_a_class_attribute_cannot_claim_a_privileged_boot_layer(self):
        """The OBJECT cap — the sibling branch, and the one round 6 found bare.

        `_resolve_boot_layer` has two guards from the same commit: one for the
        explicit argument (tested above) and one for the object's own attribute,
        which its docstring calls "the load-bearing part". Only the first had a
        test. An attribute on a plugin object has passed no gate at all — no
        PluginRecord, so neither the origin check nor the tenant-scope downgrade
        has run.
        """
        class _SelfPromoting(_Plug):
            boot_layer = "compliance"

        self.reg.register(_SelfPromoting("promoter"), _ctx("promoter"))

        self.assertIs(
            self.reg.boot_layer_of("promoter"), BootLayer.INSTALLED,
            "a plugin promoted itself to the compliance boot layer with a class "
            "attribute — it would be permanently undisableable",
        )
        self.assertTrue(self.reg.can_disable("promoter"))
        self.reg.disable("promoter")  # must not raise PluginDisableRefused

    def test_an_unprivileged_self_declaration_is_still_honoured(self):
        # Counter-test: capping everything would make the attribute useless.
        class _Bundled(_Plug):
            boot_layer = "bundled"

        self.reg.register(_Bundled("bundled-one"), _ctx("bundled-one"))
        self.assertIs(self.reg.boot_layer_of("bundled-one"), BootLayer.BUNDLED)

    def test_the_bootstrap_path_can_still_assign_a_privileged_layer(self):
        # Counter-test: refusing the argument outright would break
        # bootstrap_global, which is the one legitimate assigner.
        self.reg.register(_Plug("gate"), _ctx("gate"), boot_layer=BootLayer.COMPLIANCE)
        self.assertIs(self.reg.boot_layer_of("gate"), BootLayer.COMPLIANCE)


# ── 4. Lifecycle operations for one plugin do not interleave ─────────────────


class TestLifecycleIsSerialisedPerPlugin(_Base):
    """`on_unload` used to be able to run while `on_load` was still going.

    The registry lock guards the maps and is released before any call into
    plugin code — right, because a slow on_load must not freeze the registry
    for everyone. But it left register and unregister interleaving for the SAME
    plugin: both reported success, the plugin ended up gone, whatever it opened
    stayed open (shutdown could no longer reach it, the id was not in
    discover()), and the append-only audit chain recorded `unloaded` before
    `loaded`.
    """

    def test_unregister_waits_for_a_slow_load_instead_of_cutting_into_it(self):
        import threading
        import time

        order: list[str] = []
        started = threading.Event()

        class _Slow(_Plug):
            def on_load(self, ctx):
                order.append("on_load:start")
                started.set()
                time.sleep(0.15)
                order.append("on_load:end")

            def on_unload(self):
                order.append("on_unload")

        plugin = _Slow("slow")
        errors: list[BaseException] = []

        def _load():
            try:
                self.reg.register(plugin, _ctx("slow"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def _unload():
            started.wait(timeout=2)
            try:
                self.reg.unregister("slow")
            except Exception:  # noqa: BLE001 — losing the race is a valid outcome
                pass

        t1, t2 = threading.Thread(target=_load), threading.Thread(target=_unload)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertNotIn(
            "on_unload", order[:order.index("on_load:end")],
            f"on_unload ran before on_load finished: {order}",
        )

    def test_the_audit_chain_cannot_record_unloaded_before_loaded(self):
        import threading
        import time

        events: list[str] = []
        started = threading.Event()

        def _emit(event_type: str, details: dict) -> None:
            if event_type in ("plugin.loaded", "plugin.unloaded"):
                events.append(event_type)

        ctx = PluginContext(
            plugin_id="slow2", tenant_id="_default", corvin_home=Path("/tmp"),
            config={}, audit_emit=_emit,
        )

        class _Slow(_Plug):
            def on_load(self, c):
                started.set()
                time.sleep(0.15)

        t1 = threading.Thread(target=lambda: self.reg.register(_Slow("slow2"), ctx))
        t1.start()
        started.wait(timeout=2)
        try:
            self.reg.unregister("slow2")
        except Exception:  # noqa: BLE001
            pass
        t1.join(timeout=5)

        if "plugin.unloaded" in events:
            self.assertLess(
                events.index("plugin.loaded"), events.index("plugin.unloaded"),
                f"the append-only chain claims unloaded before loaded: {events}",
            )


if __name__ == "__main__":
    unittest.main()
