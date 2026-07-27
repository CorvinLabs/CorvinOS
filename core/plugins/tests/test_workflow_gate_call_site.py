"""The `workflow.workflow_gate` call site (ADR-0251 D1/D2/D3).

The only fail-closed point, and the only one where "the hook could not answer"
must produce a denial rather than the default. The reasoning is asymmetric with
the other three on purpose: there the caller wants an optimisation and the
bundled answer is a fine substitute, here the caller is asking "may this run at
all", and a gate that cannot answer has not said yes.

These drive `_stream_run` directly rather than through the HTTP route: the
assertion is about what the generator does before it touches a node, and a route
test would add auth, tenancy and SSE framing between the test and the claim.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CONSOLE = _REPO / "core" / "console"
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_plugins import extension_points as ep  # noqa: E402

_WF_YAML = """\
awp: "1.0.0"
workflow:
  name: gate-test
  version: "1.0.0"
orchestration:
  engine: graph
  graph:
    - id: first
      agent: noop
    - id: second
      agent: noop
"""


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
        from corvin_console.routes import workflows as wf

        self.wf = wf

    def tearDown(self) -> None:
        ep.clear_all()
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _hook(self, fn, plugin_id="p1"):
        ep.register_hook(
            "workflow.workflow_gate", fn, plugin_id=plugin_id, tenant_id="_default"
        )

    def _run(self, *, dry_run=True) -> list[dict]:
        """Drive the SSE generator and return the decoded events."""
        async def _drive():
            out = []
            gen = self.wf._stream_run(
                "_default", "fp", "wid-1", "rid-1", _WF_YAML, {}, dry_run,
            )
            async for chunk in gen:
                for line in chunk.splitlines():
                    if line.startswith("data: "):
                        out.append(json.loads(line[6:]))
            return out

        return asyncio.run(_drive())

    @staticmethod
    def _types(events):
        return [e.get("type") for e in events]


class TestAHookMayDeny(_Base):
    def test_false_stops_the_run_before_any_node(self):
        self._hook(lambda w: False)
        events = self._run()
        self.assertIn("error", self._types(events))
        self.assertNotIn(
            "node_started", self._types(events),
            "a denied workflow still touched a node",
        )

    def test_a_dry_run_is_gated_too(self):
        """A gate answers "may this run at all".

        Letting a denied workflow still be enumerated would make the answer
        depend on a query parameter — and the placement (before the `dry_run`
        branch) is the whole reason this test exists.
        """
        self._hook(lambda w: False)
        self.assertNotIn("node_started", self._types(self._run(dry_run=True)))

    def test_true_lets_the_run_proceed(self):
        self._hook(lambda w: True)
        self.assertIn("node_started", self._types(self._run()))

    def test_the_denial_is_recorded_in_the_run_meta(self):
        self._hook(lambda w: False)
        self._run()
        meta = json.loads(
            self.wf._run_meta_path("_default", "wid-1", "rid-1").read_text()
        )
        self.assertEqual(meta["status"], "failed")
        self.assertIs(meta["ok"], False)


class TestFailClosed(_Base):
    def test_a_raising_hook_denies(self):
        def _boom(w):
            raise RuntimeError("hostile")

        self._hook(_boom)
        self.assertNotIn("node_started", self._types(self._run()))

    def test_a_wrong_return_type_denies(self):
        # D3: "the gate returned a dict" and "the gate crashed" are the same
        # event from the call site's view — no decision was produced.
        self._hook(lambda w: "yes")
        self.assertNotIn("node_started", self._types(self._run()))

    def test_none_abstains_and_the_run_proceeds(self):
        # NOT a denial. A gate that cares about some workflows and not others
        # would otherwise have to return True for every run it has no opinion
        # about, overriding the core gate on all of them.
        self._hook(lambda w: None)
        self.assertIn("node_started", self._types(self._run()))


class TestWhatTheHookIsGiven(_Base):
    def test_it_sees_the_structure_and_not_the_inputs(self):
        seen: list[dict] = []

        def _h(w):
            seen.append(w)
            return True

        self._hook(_h)
        self._run()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["node_ids"], ["first", "second"])
        self.assertEqual(seen[0]["node_count"], 2)
        self.assertEqual(seen[0]["wid"], "wid-1")
        # `inputs` is user-supplied and routinely carries the task text. Handing
        # it to every installed plugin on every run would make a gate hook a
        # content tap, which is a different capability from gating.
        self.assertNotIn("inputs", seen[0])
        self.assertNotIn("yaml_text", seen[0])


class TestFlagOffIsThePreFeaturePath(_Base):
    FLAG = False

    def test_a_denying_hook_does_not_run(self):
        """The ship-dark requirement, on the fail-closed point specifically.

        With the feature off the point is ABSENT, not denying — otherwise
        installing a gate plugin and leaving the flag off would silently brick
        every workflow on the install.
        """
        called: list[int] = []

        def _h(w):
            called.append(1)
            return False

        self._hook(_h)
        self.assertIn("node_started", self._types(self._run()))
        self.assertEqual(called, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
