"""The `delegation.route_selection_policy` call site (ADR-0251 D1/D2).

The bundled classifier answers a boolean — "is this task shaped for the ACS
fan-out?" — and the point's declared type is a route string, so
`delegation_policy.resolve_delegation_route` is where the two vocabularies meet.

The asymmetry these tests pin: a hook may **suppress** delegation and may never
**cause** it. That is not a style choice. A hook that could answer `"acs"` on a
turn the classifier declined would be a plugin routing work into a delegation
engine on its own authority — spending the operator's quota through a decision
the operator's own classifier refused — and CLAUDE.md requires every degrade
ladder to end at `native`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from corvin_plugins import extension_points as ep  # noqa: E402

import delegation_policy as dp  # noqa: E402  # isort: skip


class _Base(unittest.TestCase):
    FLAG = True

    def setUp(self) -> None:
        ep.clear_all()
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name)
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        g = home / "tenants" / "_default" / "global"
        g.mkdir(parents=True)
        (g / "features.json").write_text(
            json.dumps({"flags": {ep.FLAG_ID: self.FLAG}}), encoding="utf-8"
        )
        self.audited: list[tuple[str, dict]] = []
        self._real = dp._audit_refusal

        def _sink(event_type: str, details: dict, *, tenant_id: str) -> None:
            self.audited.append((event_type, details))

        dp._audit_refusal = _sink  # type: ignore[assignment]

    def tearDown(self) -> None:
        dp._audit_refusal = self._real  # type: ignore[assignment]
        ep.clear_all()
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _hook(self, fn, plugin_id="p1"):
        ep.register_hook(
            "delegation.route_selection_policy", fn,
            plugin_id=plugin_id, tenant_id="_default",
        )


class TestAHookMaySuppress(_Base):
    def test_native_suppresses_a_delegation_the_classifier_wanted(self):
        self._hook(lambda turn: "native")
        self.assertFalse(dp.resolve_delegation_route(True, tenant_id="_default"))
        self.assertEqual(self.audited, [], "suppression is not a refusal")

    def test_confirming_the_bundled_route_changes_nothing(self):
        self._hook(lambda turn: "acs")
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))

    def test_the_hook_sees_the_bundled_route_not_the_boolean(self):
        seen: list[dict] = []

        def _h(turn):
            seen.append(turn)
            return None

        self._hook(_h)
        dp.resolve_delegation_route(True, tenant_id="_default")
        dp.resolve_delegation_route(False, tenant_id="_default")
        self.assertEqual([s["bundled"] for s in seen], ["acs", "native"])


class TestAHookMayNotCauseDelegation(_Base):
    def test_acs_on_a_declined_turn_is_refused(self):
        """The load-bearing one, and the direction that costs money.

        `"acs"` is a well-typed route the bus cannot fault. Only this module
        knows the classifier declined this turn, which makes the answer a
        widening rather than an input.
        """
        self._hook(lambda turn: "acs", plugin_id="greedy")
        self.assertFalse(dp.resolve_delegation_route(False, tenant_id="_default"))

    def test_the_refusal_is_audited(self):
        self._hook(lambda turn: "acs", plugin_id="greedy")
        dp.resolve_delegation_route(False, tenant_id="_default")
        events = [e for e in self.audited if e[0] == "plugin.extension_route_refused"]
        self.assertEqual(len(events), 1, self.audited)
        self.assertEqual(events[0][1]["bundled"], "native")
        self.assertEqual(events[0][1]["refused"], "acs")

    def test_tde_is_refused_in_both_directions(self):
        # Not a route this call site ever produces: the classifier's vocabulary
        # is ACS-or-not. A hook naming `tde` here is choosing an engine, which
        # is the OTHER point's business and bounded by the operator's mode there.
        self._hook(lambda turn: "tde")
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))
        self.assertFalse(dp.resolve_delegation_route(False, tenant_id="_default"))

    def test_a_non_route_string_is_not_echoed_into_the_chain(self):
        self._hook(lambda turn: "../../etc/passwd")
        self.assertFalse(dp.resolve_delegation_route(False, tenant_id="_default"))
        events = [e for e in self.audited if e[0] == "plugin.extension_route_refused"]
        self.assertEqual(events[0][1]["refused"], "<not-a-route>")
        self.assertNotIn("passwd", json.dumps(events[0][1]))


class TestAbstentionAndFailure(_Base):
    def test_none_leaves_the_classifier_alone(self):
        self._hook(lambda turn: None)
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))
        self.assertFalse(dp.resolve_delegation_route(False, tenant_id="_default"))

    def test_a_raising_hook_costs_the_opinion_not_the_turn(self):
        # Not a fail-closed point: the caller wants a routing preference, and
        # the bundled classifier is a perfectly good answer.
        def _boom(turn):
            raise RuntimeError("hostile")

        self._hook(_boom)
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))

    def test_a_wrong_type_costs_the_opinion_not_the_turn(self):
        self._hook(lambda turn: {"route": "native"})
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))


class TestFlagOffIsThePreFeaturePath(_Base):
    FLAG = False

    def test_no_hook_runs(self):
        called: list[int] = []

        def _h(turn):
            called.append(1)
            return "native"

        self._hook(_h)
        self.assertTrue(dp.resolve_delegation_route(True, tenant_id="_default"))
        self.assertEqual(called, [], "a hook ran with the feature flag off")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
