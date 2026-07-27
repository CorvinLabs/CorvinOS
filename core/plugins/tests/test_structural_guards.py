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
