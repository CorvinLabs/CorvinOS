"""The `engine.engine_selection` call site (ADR-0251 D1/D2).

`test_extension_points.py` proves the bus dispatches. This proves the shared
`delegation_policy` module actually calls it, and — the part that matters — that
the refusal in D2 is made HERE rather than trusted to the hook.

The distinction the tests below turn on: the bus knows a hook returned a `str`.
Only this module knows which strings are engines and which of them the operator
selected. So a hook answering `"tde"` on a `native` install is a well-typed
answer that the bus cannot fault and the call site must refuse.
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
    """Isolated CORVIN_HOME with the flag in a known state, empty bus."""

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

    def _resolve(self, **kw):
        base = dict(
            mode="native", force_delegate=False, is_big_data=False,
            tde_available=False, quota_ok=False, tenant_id="_default",
        )
        base.update(kw)
        return dp.resolve_worker_engine(**base)


class TestTheHookIsActuallyConsulted(_Base):
    def test_a_hook_can_de_escalate_to_native(self):
        # acs -> native. The one direction D2 permits.
        ep.register_hook(
            "engine.engine_selection", lambda req: "native",
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(self._resolve(mode="acs"), "native")

    def test_the_hook_receives_the_bundled_answer_and_the_operator_mode(self):
        seen: list[dict] = []

        def _hook(req):
            seen.append(req)
            return None

        ep.register_hook(
            "engine.engine_selection", _hook, plugin_id="p1", tenant_id="_default"
        )
        self._resolve(mode="acs")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["mode"], "acs")
        self.assertEqual(seen[0]["bundled"], "acs")


class TestD2RefusalIsMadeAtTheCallSite(_Base):
    def test_a_hook_may_not_escalate_to_an_engine_the_operator_did_not_select(self):
        """The load-bearing one. `"tde"` is a perfectly well-typed `str`.

        The bus cannot fault it — only this module knows it is an engine the
        operator did not select. CLAUDE.md § Worker Engine Selection: no degrade
        path may route into an engine the operator did not choose, and a hook is
        a degrade path's input.
        """
        ep.register_hook(
            "engine.engine_selection", lambda req: "tde",
            plugin_id="hostile", tenant_id="_default",
        )
        self.assertEqual(self._resolve(mode="native"), "native")

    def test_the_refusal_is_audited_with_the_mode_and_the_rejected_engine(self):
        ep.register_hook(
            "engine.engine_selection", lambda req: "tde",
            plugin_id="hostile", tenant_id="_default",
        )
        self._resolve(mode="native")
        events = [e for e in self.audited if e[0] == "plugin.extension_engine_refused"]
        self.assertEqual(len(events), 1, self.audited)
        self.assertEqual(events[0][1]["operator_mode"], "native")
        self.assertEqual(events[0][1]["bundled"], "native")
        self.assertEqual(events[0][1]["refused"], "tde")

    def test_a_hook_may_not_re_assert_the_mode_over_an_availability_degrade(self):
        """mode=tde but TDE is unavailable -> the rule says native.

        A hook answering "tde" here would be overriding a degrade it cannot
        observe. This is why `permitted_engines` does not include `mode`.
        """
        ep.register_hook(
            "engine.engine_selection", lambda req: "tde",
            plugin_id="p1", tenant_id="_default",
        )
        got = self._resolve(mode="tde", tde_available=False, quota_ok=False)
        self.assertEqual(got, "native")

    def test_a_non_engine_string_is_refused_and_not_echoed_verbatim(self):
        ep.register_hook(
            "engine.engine_selection", lambda req: "/etc/passwd",
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(self._resolve(mode="native"), "native")
        events = [e for e in self.audited if e[0] == "plugin.extension_engine_refused"]
        self.assertEqual(events[0][1]["refused"], "<not-an-engine>")
        # A free-form string from a plugin must not land in an append-only chain.
        self.assertNotIn("/etc/passwd", json.dumps(events[0][1]))

    def test_confirming_the_bundled_answer_is_not_a_refusal(self):
        ep.register_hook(
            "engine.engine_selection", lambda req: "acs",
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(self._resolve(mode="acs"), "acs")
        self.assertEqual(self.audited, [])


class TestFlagOffIsThePreFeaturePath(_Base):
    FLAG = False

    def test_no_hook_runs_and_the_bundled_rule_decides(self):
        called: list[int] = []

        def _hook(req):
            called.append(1)
            return "native"

        ep.register_hook(
            "engine.engine_selection", _hook, plugin_id="p1", tenant_id="_default"
        )
        # mode=acs would be de-escalated to native by the hook if it ran.
        self.assertEqual(self._resolve(mode="acs"), "acs")
        self.assertEqual(called, [], "a hook ran with the feature flag off")

    def test_every_bundled_route_is_unchanged_with_the_flag_off(self):
        # The flag-off state has to be tested, not assumed: a flag only ever
        # exercised in one state rots (CLAUDE.md § Feature Flags).
        cases = [
            (dict(mode="native"), "native"),
            (dict(mode="native", is_big_data=True), "acs"),
            (dict(mode="native", force_delegate=True), "acs"),
            (dict(mode="acs"), "acs"),
            (dict(mode="tde", tde_available=True, quota_ok=True), "tde"),
            (dict(mode="tde", tde_available=False, quota_ok=True), "native"),
            (dict(mode="bogus"), "native"),
        ]
        for kw, expected in cases:
            with self.subTest(**kw):
                self.assertEqual(self._resolve(**kw), expected)


class TestThePureRuleStayedPure(_Base):
    def test_worker_engine_target_consults_no_hook(self):
        """The routing matrix must remain a pure function.

        It is the shared source of truth every surface unit-tests against, and
        a hook inside it would make those tests depend on process-wide bus
        state. `resolve_worker_engine` is the composed entry; this is the rule.
        """
        ep.register_hook(
            "engine.engine_selection", lambda req: "native",
            plugin_id="p1", tenant_id="_default",
        )
        self.assertEqual(
            dp.worker_engine_target(
                mode="acs", force_delegate=False, is_big_data=False,
                tde_available=False, quota_ok=False,
            ),
            "acs",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
