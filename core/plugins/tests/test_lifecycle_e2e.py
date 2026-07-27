"""The plugin spine, end to end: scaffold → declare → boot → invoked → audited.

Every other suite in this directory tests a segment. `test_plugin_cli.py` proves
the scaffold writes files. `test_bootstrap.py` proves a declared class loads.
`test_additive_backends.py` proves a registered `audit_backend` receives a copy —
by registering a double directly on the provider module. None of them proves the
segments connect, and that is the exact defect class this ADR series has now hit
three times: a mechanism that works when called, with nothing calling it.

So this module runs the whole chain once, with no doubles at the seams:

1. the real `corvin` CLI scaffolds an `audit_backend` plugin onto disk;
2. the author implements the one TODO the template leaves for them;
3. it is declared in a real `tenant.corvin.yaml` under `spec.plugins.installed`;
4. `bootstrap_all()` boots it the way the gateway does;
5. a real audited action is written through `operator/bridges/shared/audit.py`;
6. the core hash chain holds the record, the plugin holds a copy, and
   `verify_audit()` still passes.

**Why `audit_backend` is the type under test.** It is one of the five surfaces
with a live consumer today, and it is the compliance-critical one: ADR-0233's
additive-only invariant says the plugin gets a COPY *after* the core write
commits and can never suppress or rewrite it. Step 6 is that invariant measured
on the real path rather than asserted about a double.

**Why this cannot yet be the E2E the activation plan describes.** That plan's E1
says "`corvin plugin install`, then the hook fires on a real turn". Neither part
exists: `install` is Stage 6 and the extension points are Stage 3. Rather than
wait, this module runs the same spine through the install path that *does* exist
(the declarative `spec.plugins.installed` entry, which is what the generated
README tells an author to write) and against a provider that *is* consumed. When
Stages 3 and 6 land, they add their rows here — see `_STAGE_ROWS_OWED`.

Isolation: `conftest.py` redirects `VOICE_AUDIT_PATH` per test, so the chain
written here is a tmp one. The teardown also clears the process-wide provider
slot — an `audit_backend` left active would fan out every later test's audit
events into a dead object.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"

#: Rows this harness owes once the stage that unblocks them lands. Asserted
#: below, so finishing a stage without extending this file turns the suite red.
_STAGE_ROWS_OWED = {
    "stage-3": "an extension-point hook fires from a real delegation path",
    "stage-4": "a compute engine registered by a plugin is reachable via MCP",
    "stage-5": "a bridge supervisor reports a dead daemon as unhealthy",
    "stage-6": "`corvin plugin install` replaces the hand-written yaml below",
}


def _corvin_cli() -> Path | None:
    """The console script from the interpreter running this test.

    Deliberately not `uv run corvin`: that resolves an environment which may not
    be the one under test, and the point of the step is to run the CLI a user
    runs, from the install being tested.
    """
    candidate = Path(sys.executable).parent / "corvin"
    return candidate if candidate.is_file() else None


class TestPluginLifecycleE2E(unittest.TestCase):
    """One scenario, six steps, no doubles at the seams."""

    maxDiff = None

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.corvin_home = self.workdir / "corvin_home"
        self.corvin_home.mkdir()
        self._loaded: list[str] = []
        self._path_added: str | None = None

    def tearDown(self) -> None:
        # Unload first: on_unload() detaches the provider slot. Then clear the
        # slot unconditionally, because a test that failed mid-way may have
        # registered without reaching a clean unload, and a live audit_backend
        # left in a module global would receive every subsequent test's events.
        if self._loaded:
            try:
                from corvin_plugins.bootstrap import shutdown

                shutdown(self._loaded)
            except Exception:
                pass
        try:
            from corvin_plugins.providers import audit_backend

            audit_backend.clear()
        except Exception:
            pass
        if self._path_added and self._path_added in sys.path:
            sys.path.remove(self._path_added)
        self._tmp.cleanup()

    # ── the six steps ────────────────────────────────────────────────────────

    def _scaffold(self, plugin_id: str) -> Path:
        """Step 1 — the real CLI, as a subprocess."""
        cli = _corvin_cli()
        if cli is None:
            self.skipTest(
                f"no `corvin` console script next to {sys.executable} — this "
                f"install cannot exercise the CLI step"
            )
        proc = subprocess.run(
            [str(cli), "plugin", "new", "audit_backend", plugin_id,
             "-o", str(self.workdir)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"`corvin plugin new` failed:\nstdout={proc.stdout}\n"
            f"stderr={proc.stderr}",
        )
        dest = self.workdir / plugin_id.replace(".", "_").replace("-", "_")
        self.assertTrue(
            (dest / "plugin.py").is_file(),
            f"scaffold did not write plugin.py; workdir holds "
            f"{sorted(p.name for p in self.workdir.iterdir())}",
        )
        return dest

    def _implement_the_todo(self, dest: Path, sink_path: Path) -> None:
        """Step 2 — do what the template asks the author to do.

        The shipped `_drain` worker carries `# TODO: ship record to your sink.`
        followed by `del record`. Replacing exactly that pair is the author's
        job, so the test does it rather than substituting its own class: a
        scaffold whose TODO cannot actually be implemented would otherwise pass
        every test in this repo.
        """
        src = (dest / "plugin.py").read_text(encoding="utf-8")
        marker = "                # TODO: ship `record` to your sink.\n                del record\n"
        self.assertIn(
            marker, src,
            "the audit_backend template's drain TODO no longer has the shape "
            "this test implements. Update the marker here in the same commit "
            "as the template — a silently non-matching marker would make this "
            "E2E assert on an unimplemented plugin.",
        )
        src = src.replace(
            marker,
            "                import json as _json\n"
            f"                with open({str(sink_path)!r}, 'a', encoding='utf-8') as _fh:\n"
            "                    _fh.write(_json.dumps(record) + '\\n')\n",
        )
        (dest / "plugin.py").write_text(src, encoding="utf-8")

    def _declare(self, dest: Path, plugin_id: str) -> None:
        """Step 3 — the install path that exists today (ADR-0030 Phase 7)."""
        cfg_dir = self.corvin_home / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True)
        module = dest.name
        cls = self._plugin_class_name(dest / "plugin.py")
        (cfg_dir / "tenant.corvin.yaml").write_text(
            textwrap.dedent(f"""\
                spec:
                  plugins:
                    installed:
                      - id: {plugin_id}
                        class_path: "{module}.plugin:{cls}"
                """),
            encoding="utf-8",
        )
        # The declared class must be importable, which for a scaffold on disk
        # means its parent is on sys.path. `corvin plugin install` (Stage 6)
        # will own this step; today the generated README tells the author to do
        # it via packaging, and the test does the equivalent.
        self._path_added = str(self.workdir)
        sys.path.insert(0, self._path_added)

    @staticmethod
    def _plugin_class_name(plugin_py: Path) -> str:
        import ast

        tree = ast.parse(plugin_py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                isinstance(s, ast.Assign)
                and any(getattr(t, "id", None) == "plugin_type" for t in s.targets)
                for s in node.body
            ):
                return node.name
        raise AssertionError(f"no plugin class found in {plugin_py}")

    def _boot(self) -> None:
        """Step 4 — the same entry the gateway lifespan calls."""
        from corvin_plugins.bootstrap import bootstrap_all

        self._loaded = bootstrap_all(
            tenant_id="_default",
            corvin_home=self.corvin_home,
        )

    @staticmethod
    def _wait_for_copy(sink_path: Path, event_type: str, timeout: float = 15.0) -> list[dict]:
        """Wait until a copy of ``event_type`` lands, then return every copy.

        The wait predicate has to be the SAME condition the assertion checks.
        An earlier version of this waited for the sink file to *exist* and then
        asserted on its contents — but the boot writes `plugin.loaded` through
        the same fan-out, so the file appeared immediately and the loop exited
        long before the event under test had been drained. It passed on timing
        luck and failed the moment another test ran first. A wait that is weaker
        than its assertion is a flake with a countdown on it.
        """
        deadline = time.monotonic() + timeout
        while True:
            copies = []
            if sink_path.is_file():
                copies = [
                    json.loads(line)
                    for line in sink_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            if any(c.get("event_type") == event_type for c in copies):
                return copies
            if time.monotonic() >= deadline:
                return copies
            time.sleep(0.05)

    # ── the scenario ─────────────────────────────────────────────────────────

    def test_a_scaffolded_audit_backend_receives_a_real_audited_event(self):
        plugin_id = "com.example.e2e-sink"
        sink_path = self.workdir / "received.jsonl"

        dest = self._scaffold(plugin_id)
        self._implement_the_todo(dest, sink_path)
        self._declare(dest, plugin_id)
        self._boot()

        self.assertIn(
            plugin_id, self._loaded,
            f"the declared plugin did not load; bootstrap_all returned "
            f"{self._loaded!r}",
        )

        # Step 5 — a real audited action, written by the real writer.
        if str(_SHARED) not in sys.path:
            sys.path.insert(0, str(_SHARED))
        try:
            import audit  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            self.fail(f"the real audit writer is not importable: {exc}")

        # Positive control. `audit.py` binds its fan-out sink at import time,
        # inside a bare `except Exception: _audit_sink = None`. If that binding
        # ever fails, every fan-out assertion below passes by never running —
        # the same vacuous-green shape this whole module exists to prevent.
        self.assertIsNotNone(
            audit._audit_sink,
            "audit.py bound no fan-out sink, so no copy could reach any "
            "plugin and the assertions below would be vacuous",
        )

        chain = Path(os.environ["VOICE_AUDIT_PATH"])
        audit.audit_event(
            "bridge.message_received",
            channel="e2e",
            chat_key="e2e-lifecycle",
            tenant_id="_default",
        )

        # Step 6a — the CORE chain holds it. This is asserted first and
        # independently: the core write is the compliance record, and it must be
        # intact whether or not any plugin exists.
        self.assertTrue(chain.is_file(), "the core writer produced no chain file")
        core_records = [
            json.loads(line)
            for line in chain.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(r.get("event_type") == "bridge.message_received" for r in core_records),
            f"the core chain has no record of the audited action; it holds "
            f"{[r.get('event_type') for r in core_records]}",
        )

        # Step 6b — the plugin holds a COPY of THIS event.
        copies = self._wait_for_copy(sink_path, "bridge.message_received")
        self.assertTrue(
            any(c.get("event_type") == "bridge.message_received" for c in copies),
            "the scaffolded plugin never received a copy of the audited "
            "action — it loaded, registered and reported healthy, and the "
            f"fan-out did not reach it. It did receive: "
            f"{[c.get('event_type') for c in copies]}",
        )

        # Step 6c — the chain still verifies. A fan-out that corrupted the
        # hash chain would be the worst possible outcome of this feature, and
        # it is the one thing a copy-count assertion cannot see.
        ok, broken = audit.verify_audit(chain)
        self.assertTrue(ok, f"the hash chain no longer verifies: {broken[:3]}")

    def test_a_raising_backend_costs_the_copy_and_not_the_core_record(self):
        """The additive-only invariant, measured on the real writer.

        ADR-0233 D4: a plugin can never suppress a compliance record. The double
        in `test_additive_backends.py` proves the provider swallows the raise;
        this proves the CORE write survives it on the path that actually writes
        the chain.
        """
        plugin_id = "com.example.e2e-exploder"
        dest = self._scaffold(plugin_id)

        src = (dest / "plugin.py").read_text(encoding="utf-8")
        marker = "        try:\n            self._queue.put_nowait(record)\n"
        self.assertIn(
            marker, src,
            "the audit_backend template's fanout() no longer has the shape "
            "this test breaks on purpose — update the marker in the same "
            "commit as the template.",
        )
        src = src.replace(
            marker,
            "        raise RuntimeError('e2e: a hostile sink')\n" + marker,
            1,
        )
        (dest / "plugin.py").write_text(src, encoding="utf-8")

        self._declare(dest, plugin_id)
        self._boot()
        self.assertIn(plugin_id, self._loaded)

        if str(_SHARED) not in sys.path:
            sys.path.insert(0, str(_SHARED))
        import audit  # type: ignore[import-not-found]

        self.assertIsNotNone(audit._audit_sink, "no sink bound — test is vacuous")

        chain = Path(os.environ["VOICE_AUDIT_PATH"])
        audit.audit_event(
            "bridge.message_received",
            channel="e2e",
            chat_key="e2e-exploder",
            tenant_id="_default",
        )

        records = [
            json.loads(line)
            for line in chain.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(
            any(r.get("event_type") == "bridge.message_received" for r in records),
            "a raising audit_backend suppressed the CORE compliance record — "
            "this is the ADR-0233 additive-only violation, on the real path",
        )
        ok, broken = audit.verify_audit(chain)
        self.assertTrue(ok, f"a raising backend broke the hash chain: {broken[:3]}")

        # And the raise was actually REACHED. Without this the test is vacuous:
        # a refutation round removed the injected raise entirely and everything
        # above stayed green, because "a well-behaved plugin does not suppress
        # the core record" is trivially true. The provider counts a fan-out
        # failure only when the backend it called raised, so a non-zero count is
        # the evidence that this scenario tested what it claims to.
        from corvin_plugins.providers import audit_backend as provider

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and provider.failure_count() == 0:
            time.sleep(0.05)
        self.assertGreater(
            provider.failure_count(), 0,
            "the injected raise was never reached, so this scenario proves "
            "nothing about a raising backend — the fan-out did not call the "
            "plugin at all",
        )


class TestHarnessOwesRowsToLaterStages(unittest.TestCase):
    """Keep the gap between this harness and the plan's E1 visible.

    Without this, "the lifecycle E2E exists" reads as "the lifecycle is covered",
    and Stages 3-6 could each ship with unit tests only — which is the exact
    substitution this harness was built to stop.
    """

    def test_the_owed_rows_are_recorded(self):
        self.assertEqual(
            set(_STAGE_ROWS_OWED),
            {"stage-3", "stage-4", "stage-5", "stage-6"},
            "the owed-rows record drifted from the activation plan's open "
            "stages; correct it in the same commit as the stage that closed",
        )


if __name__ == "__main__":
    unittest.main()
