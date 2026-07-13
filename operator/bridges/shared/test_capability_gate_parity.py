"""ADR-0141 Tier 3 — capability-presence gate PARITY between the bridge
adapter and the owner console.

The gate is implemented as two independent, hand-copied blocks:
  * ``operator/bridges/shared/adapter.py::_check_capabilities_or_fail``
  * ``core/console/corvin_console/_spawn_gates.py::_check_capabilities_or_fail``
    (whose own docstring says the capability block is "lifted VERBATIM from
    chat_runtime" rather than imported).

Both wrap ``security_capabilities.assert_capabilities_present()`` /
``CapabilityMissingError`` / ``bootstrap_core_capabilities()`` in their own
try/except/bootstrap/reassert control flow. Nothing anywhere previously
cross-checked that the two copies actually behave the same way on the SAME
missing-capability condition — this suite closes that blind spot.

Run: python3 operator/bridges/shared/test_capability_gate_parity.py
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "core" / "console"))

import adapter  # noqa: E402
import security_capabilities as sc  # noqa: E402
from corvin_console import _spawn_gates  # noqa: E402


class CapabilityGateParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cap-gate-parity-")
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        # Snapshot the live registry (production boot state) so we can fully
        # restore it afterwards -- other test modules in this process rely on
        # the mandatory capabilities staying registered.
        self._registry_snapshot = dict(sc._REGISTRY)
        sc._clear_registry()
        sc.bootstrap_core_capabilities()

        self._patches: list[tuple] = []

    def _patch(self, obj, attr: str, value) -> None:
        self._patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def tearDown(self) -> None:
        for obj, attr, original in reversed(self._patches):
            setattr(obj, attr, original)
        sc._clear_registry()
        sc._REGISTRY.update(self._registry_snapshot)
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

    # ── (1) baseline parity: fully registered -> BOTH surfaces permit ────────
    def test_fully_registered_permits_both_surfaces(self) -> None:
        self.assertIsNone(
            adapter._check_capabilities_or_fail(channel="discord", chat_key="c1")
        )
        self.assertIsNone(
            _spawn_gates._check_capabilities_or_fail(channel="web", chat_key="c1")
        )

    # ── (2) transient gap (spawn reached before boot's explicit bootstrap):
    #        BOTH copies must independently self-heal via lazy bootstrap ─────
    def test_transient_gap_recovered_by_lazy_bootstrap_both_surfaces(self) -> None:
        sc._clear_registry()
        # All layer modules are already imported (sys.modules), so the REAL
        # bootstrap_core_capabilities() can re-register everything -- exactly
        # the "unit tests / an engine path reached before all layer modules
        # were imported" scenario both docstrings describe.
        self.assertIsNone(
            adapter._check_capabilities_or_fail(channel="discord", chat_key="c1"),
            "adapter must self-heal a transient (pre-bootstrap) registry gap",
        )
        sc._clear_registry()  # reset so console exercises its OWN bootstrap call
        self.assertIsNone(
            _spawn_gates._check_capabilities_or_fail(channel="web", chat_key="c1"),
            "console must self-heal a transient (pre-bootstrap) registry gap",
        )

    # ── (3) permanently missing capability (deleted layer file, survives
    #        bootstrap): BOTH copies must refuse, and BOTH must have actually
    #        run their own bootstrap-then-reassert control flow ─────────────
    def test_permanently_missing_capability_refuses_both_surfaces(self) -> None:
        sc._clear_registry()
        for cap in sc.MANDATORY_CAPABILITIES:
            if cap != sc.CAP_HOUSE_RULES:
                sc.register_capability(cap, version=sc.CAP_VERSIONS[cap])
        # house_rules is left unregistered -- simulates its file being
        # deleted/renamed on disk, which bootstrap can never recover.

        calls = {"n": 0}

        def _stub_bootstrap():
            calls["n"] += 1
            return {c: sc.is_registered(c) for c in sc.MANDATORY_CAPABILITIES}

        self._patch(sc, "bootstrap_core_capabilities", _stub_bootstrap)

        adapter_refusal = adapter._check_capabilities_or_fail(
            channel="discord", chat_key="c1"
        )
        self.assertIsNotNone(
            adapter_refusal,
            "adapter must refuse when a mandatory capability is permanently missing",
        )
        self.assertGreaterEqual(
            calls["n"], 1,
            "adapter's bootstrap-then-reassert control flow must call bootstrap",
        )

        console_refusal = _spawn_gates._check_capabilities_or_fail(
            channel="web", chat_key="c1"
        )
        self.assertIsNotNone(
            console_refusal,
            "console must refuse when a mandatory capability is permanently missing",
        )
        self.assertGreaterEqual(
            calls["n"], 2,
            "console's OWN bootstrap-then-reassert control flow must "
            "independently call bootstrap too (not rely on the adapter's call)",
        )

    # ── (4) BUG (confirmed): audit-event emission diverges between the two
    #        copies on the identical missing-capability condition ───────────
    def test_capability_missing_audit_event_only_fires_on_adapter_side(self) -> None:
        """The console's own docstring
        (core/console/corvin_console/_spawn_gates.py, lines ~43 and ~124)
        claims ``security.capability_missing`` is "emitted by the LIP
        registry inside assert_capabilities_present()". It is not: the
        registry function has no audit side-effect at all, and the console's
        own ``_check_capabilities_or_fail`` never calls an audit emitter
        either -- only the bridge adapter's copy does. So the SAME
        CRITICAL, GDPR Art. 30/32 fail-closed audit event is written for the
        bridge surface and silently dropped for the console surface."""
        registry_src = inspect.getsource(sc.assert_capabilities_present)
        self.assertNotIn(
            "audit_event", registry_src,
            "the registry function has no audit side-effect -- confirms the "
            "console docstring's claim is inaccurate",
        )

        adapter_src = inspect.getsource(adapter._check_capabilities_or_fail)
        self.assertIn("_audit_event(", adapter_src)
        self.assertIn("security.capability_missing", adapter_src)

        console_src = inspect.getsource(_spawn_gates._check_capabilities_or_fail)
        self.assertNotIn(
            "audit_event", console_src,
            "confirmed divergence: the console capability gate never writes "
            "security.capability_missing to the audit chain, unlike the "
            "bridge adapter's copy of the same gate",
        )

        # Dynamic confirmation: the adapter's refusal path really does invoke
        # the audit emitter with the expected event type.
        sc._clear_registry()
        for cap in sc.MANDATORY_CAPABILITIES:
            if cap != sc.CAP_HOUSE_RULES:
                sc.register_capability(cap, version=sc.CAP_VERSIONS[cap])
        self._patch(sc, "bootstrap_core_capabilities", lambda: None)

        emitted: list[tuple] = []
        self._patch(adapter, "_audit_event",
                    lambda *a, **k: emitted.append((a, k)))

        refusal = adapter._check_capabilities_or_fail(channel="discord", chat_key="c1")
        self.assertIsNotNone(refusal)
        self.assertTrue(
            any(a and a[0] == "security.capability_missing" for a, k in emitted),
            f"expected adapter to emit security.capability_missing, got {emitted}",
        )

    # ── (5) BUG (confirmed): fail-closed guarantee diverges on an exception
    #        type OTHER than CapabilityMissingError ───────────────────────────
    def test_unexpected_exception_type_fails_closed_on_console_but_raises_on_adapter(
        self,
    ) -> None:
        """If ``security_capabilities.assert_capabilities_present`` raises
        anything OTHER than ``CapabilityMissingError`` (e.g. a signature-drift
        TypeError introduced by a future edit to the registry module), the
        console's ``_check_capabilities_or_fail`` has a catch-all
        ``except Exception`` and still fails CLOSED with a refusal string.
        The bridge adapter's copy has NO such catch-all: the exception
        propagates straight out of ``_check_capabilities_or_fail`` uncaught,
        so the bridge's spawn path does not produce the intended graceful
        fail-closed refusal (and never reaches the audit-emitting branch) on
        this exact condition -- exactly the "one surface fails closed, the
        sibling silently diverges" risk the blind-spot review flagged."""

        def _boom(*_a, **_kw):
            raise TypeError("simulated signature drift in assert_capabilities_present")

        self._patch(sc, "assert_capabilities_present", _boom)

        console_refusal = _spawn_gates._check_capabilities_or_fail(
            channel="web", chat_key="c1"
        )
        self.assertIsNotNone(
            console_refusal, "console must fail closed on ANY registry exception"
        )

        with self.assertRaises(TypeError):
            adapter._check_capabilities_or_fail(channel="discord", chat_key="c1")


if __name__ == "__main__":
    unittest.main()
