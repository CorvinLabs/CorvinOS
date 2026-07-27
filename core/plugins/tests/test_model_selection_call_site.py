"""The `engine.model_selection` call site (ADR-0251 D1/D2).

The bound a hook must meet here is not the same shape as the engine point's.
There is no `native`-style floor among models and no ordering between them, so
"may not escalate" has no meaning; the operator's setting IS the engine registry,
and registry membership is the whole bound.

That bound is checked with `engine_models.model_is_registered` — the same
fail-closed function the bundled tier resolution applies to itself. Re-deriving
it here would be two registry rules that agree until one is edited.
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

import model_selector as ms  # noqa: E402  # isort: skip


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
        self._real_write = ms._write_event

        def _sink(event_type: str, details: dict) -> None:
            self.audited.append((event_type, details))

        ms._write_event = _sink  # type: ignore[assignment]

        # The registry is a real file read; stub the ONE predicate the call site
        # consults so these tests pin the call site's rule rather than whichever
        # models this checkout happens to ship.
        import engine_models as em

        self._em = em
        self._real_registered = em.model_is_registered
        em.model_is_registered = lambda mid, eng: mid in {
            "claude-sonnet-5", "claude-haiku-4-5-20251001"
        }

    def tearDown(self) -> None:
        ms._write_event = self._real_write  # type: ignore[assignment]
        self._em.model_is_registered = self._real_registered
        ep.clear_all()
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _hook(self, fn, plugin_id="p1"):
        ep.register_hook(
            "engine.model_selection", fn, plugin_id=plugin_id, tenant_id="_default"
        )

    def _resolve(self, bundled, **kw):
        return ms.resolve_step_model(
            bundled, engine_id=kw.pop("engine_id", "claude_code"),
            tenant_id="_default", **kw
        )


class TestARegisteredModelIsAccepted(_Base):
    def test_a_hook_may_swap_to_another_registered_model(self):
        self._hook(lambda req: "claude-haiku-4-5-20251001")
        self.assertEqual(
            self._resolve("claude-sonnet-5"), "claude-haiku-4-5-20251001"
        )
        self.assertEqual(self.audited, [])

    def test_a_hook_may_name_a_model_when_the_core_had_no_opinion(self):
        """`bundled is None` means "fall through to the CLI default".

        A registered model is inside the operator's own configuration either
        way, so this is not a widening — unlike the engine point, where `None`
        has no analogue and the floor is `native`.
        """
        self.assertEqual(self._resolve(None), None)
        self._hook(lambda req: "claude-sonnet-5")
        self.assertEqual(self._resolve(None), "claude-sonnet-5")

    def test_the_hook_sees_the_bundled_answer_and_the_engine(self):
        seen: list[dict] = []

        def _h(req):
            seen.append(req)
            return None

        self._hook(_h)
        self._resolve("claude-sonnet-5", request={"surface": "console"})
        self.assertEqual(seen[0]["bundled"], "claude-sonnet-5")
        self.assertEqual(seen[0]["engine_id"], "claude_code")
        self.assertEqual(seen[0]["surface"], "console")


class TestAnUnregisteredModelIsRefused(_Base):
    def test_a_model_the_operator_never_installed_is_refused(self):
        self._hook(lambda req: "gpt-4o", plugin_id="greedy")
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")

    def test_the_refusal_is_audited_with_the_reason(self):
        self._hook(lambda req: "gpt-4o", plugin_id="greedy")
        self._resolve("claude-sonnet-5")
        events = [e for e in self.audited if e[0] == "plugin.extension_model_refused"]
        self.assertEqual(len(events), 1, self.audited)
        self.assertEqual(events[0][1]["reason"], "not_in_engine_registry")
        self.assertEqual(events[0][1]["refused"], "gpt-4o")

    def test_a_long_value_from_a_plugin_is_capped_before_the_chain(self):
        self._hook(lambda req: "x" * 5000)
        self._resolve("claude-sonnet-5")
        events = [e for e in self.audited if e[0] == "plugin.extension_model_refused"]
        self.assertLessEqual(len(events[0][1]["refused"]), 64)

    def test_a_registry_that_cannot_be_read_refuses_rather_than_admits(self):
        """A broken admissibility check keeps the bundled answer.

        Written first as `assertRaises`, which is what the code did — and the
        test name made the mismatch obvious: it claimed "refuses" while pinning
        "propagates". Propagation is the wrong contract twice over. It lets a
        broken check decide nothing at all, and the console call site turns an
        escaping exception into `model = None`, which silently DOWNGRADES the
        turn to the CLI default rather than preserving the model the core chose.
        """
        def _boom(mid, eng):
            raise RuntimeError("registry unreadable")

        self._em.model_is_registered = _boom
        self._hook(lambda req: "claude-haiku-4-5-20251001")
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")
        events = [e for e in self.audited if e[0] == "plugin.extension_model_refused"]
        self.assertEqual(len(events), 1, self.audited)


class TestAbstentionAndFailure(_Base):
    def test_none_leaves_the_bundled_answer_alone(self):
        self._hook(lambda req: None)
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")

    def test_a_raising_hook_costs_the_model_not_the_turn(self):
        def _boom(req):
            raise RuntimeError("hostile")

        self._hook(_boom)
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")

    def test_a_wrong_type_costs_the_model_not_the_turn(self):
        self._hook(lambda req: ["claude-sonnet-5"])
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")


class TestFlagOffIsThePreFeaturePath(_Base):
    FLAG = False

    def test_no_hook_runs(self):
        called: list[int] = []

        def _h(req):
            called.append(1)
            return "claude-haiku-4-5-20251001"

        self._hook(_h)
        self.assertEqual(self._resolve("claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(called, [], "a hook ran with the feature flag off")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
