"""E2E (T-0230): the console OS turn spawns where the L35 gate looked.

``chat_runtime.stream_turn`` runs against a REAL subprocess (``CORVIN_CLAUDE_BIN``): a launcher
that writes the environment it received to a file and then replays a captured claude stream.
Before the fix the console built that environment from ``os.environ`` alone, while
``check_console_spawn_or_refusal`` validated the host of the provider assigned to claude_code.
A turn could be admitted for ``llm.acme.example`` and still reach ``api.anthropic.com`` or an
inherited ``ANTHROPIC_BASE_URL``.

The egress policy used here denies ``api.anthropic.com`` and allows only the assigned provider.
Only the provider registry is stubbed (the bundled registry has no ``acme_llm``); the tenant
file, the gates, the subprocess and the audit chain are real.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

PROVIDER_HOST = "llm.acme.example"
POLICY = (
    "spec:\n"
    "  engine_models:\n    claude_code:\n      provider: {provider}\n"
    "  egress:\n    enabled: true\n    default_action: deny\n"
    "    allowed_hosts: [{allowed}]\n    forbidden_hosts: [api.anthropic.com]\n"
)


def _drain(agen):
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


@unittest.skipIf(sys.platform.startswith("win"), "POSIX launcher in the harness")
class ConsoleProviderRouteE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.home = home
        self._env_before = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"),
            "XDG_CONFIG_HOME": str(home / "xdg"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
            "CORVIN_OS_ENGINE": "claude_code",
            "ACME_LLM_KEY": "k-acme-test",
            "ANTHROPIC_BASE_URL": "https://stale.example",   # inherited: must never win
        })
        os.environ.pop("FORGE_ROOT", None)
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (home / "corvin").mkdir()
        self.cfg = home / "corvin" / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        self.cfg.parent.mkdir(parents=True)

        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        import house_rules as _hr  # type: ignore
        self._hr, self._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        self.addCleanup(setattr, _hr, "_house_rules_classifier", self._hr_orig)

        # Launcher: dump the env the spawn received, then replay a captured stream.
        env = kit.fake_env(home, "plain_no_children", speedup=50.0)
        real = Path(env["CORVIN_CLAUDE_BIN"])
        self.dump = home / "spawn-env.txt"
        wrapper = home / "fake-bin" / "claude-dump"
        wrapper.write_text(f'#!/bin/sh\nenv > "{self.dump}"\nexec "{real}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        env["CORVIN_CLAUDE_BIN"] = str(wrapper)
        os.environ.update(env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)

    def _providers(self, **spec):
        import engine_models as em  # type: ignore
        base = dict(id="acme_llm", label="Acme LLM", base_url=f"https://{PROVIDER_HOST}/v1",
                    model_source="static", credential_env="ACME_LLM_KEY", kind="cloud")
        base.update(spec)
        providers = {base["id"]: em.ProviderSpec(**base)}
        orig = em.load_providers
        em.load_providers = lambda force_reload=False: providers  # type: ignore[assignment]
        self.addCleanup(setattr, em, "load_providers", orig)

    def _turn(self) -> list[dict]:
        sess = self.cr.create_session("_default")
        return _drain(self.cr.stream_turn(sess, "hello there"))

    def _spawn_env(self) -> dict[str, str]:
        self.assertTrue(self.dump.exists(), "the claude subprocess was never started")
        return dict(line.split("=", 1) for line in self.dump.read_text().splitlines() if "=" in line)

    @staticmethod
    def _text(events: list[dict]) -> str:
        return "".join((e.get("text") or "") for e in events if e.get("type") in ("delta", "result"))

    def test_the_spawn_goes_to_the_provider_the_gate_validated(self) -> None:
        self._providers()
        self.cfg.write_text(POLICY.format(provider="acme_llm", allowed=PROVIDER_HOST), encoding="utf-8")
        self.cfg.chmod(0o600)
        events = self._turn()
        self.assertNotIn("[egress]", self._text(events), events)
        spawned = self._spawn_env()
        self.assertEqual(spawned.get("ANTHROPIC_BASE_URL"), f"https://{PROVIDER_HOST}/v1")
        self.assertEqual(spawned.get("CORVIN_CC_PROVIDER"), "acme_llm")
        self.assertEqual(spawned.get("ANTHROPIC_API_KEY"), "k-acme-test")
        self.assertNotIn("stale.example", self.dump.read_text(), "an inherited redirect reached the spawn")

    def test_a_tenant_without_an_assignment_goes_native_and_loses_an_inherited_redirect(self) -> None:
        self._providers()
        self.cfg.write_text("spec:\n  egress:\n    enabled: false\n", encoding="utf-8")
        self.cfg.chmod(0o600)
        self._turn()
        spawned = self._spawn_env()
        self.assertNotIn("ANTHROPIC_BASE_URL", spawned, "a stale redirect would bypass the egress policy")
        self.assertNotIn("CORVIN_CC_PROVIDER", spawned)

    def test_the_gate_still_refuses_a_provider_the_policy_does_not_allow(self) -> None:
        """Negative control: the egress policy, not the env, decides."""
        self._providers()
        self.cfg.write_text(POLICY.format(provider="acme_llm", allowed="other.example"), encoding="utf-8")
        self.cfg.chmod(0o600)
        events = self._turn()
        self.assertIn("[egress]", self._text(events), events)
        self.assertFalse(self.dump.exists(), "a refused turn must not spawn")

    def test_an_assigned_provider_without_a_route_refuses_instead_of_going_native(self) -> None:
        # An OpenAI-format provider needs a model to build its translating proxy; none is set.
        self._providers(id="acme_llm", model_source="openrouter")
        self.cfg.write_text(POLICY.format(provider="acme_llm", allowed=PROVIDER_HOST), encoding="utf-8")
        self.cfg.chmod(0o600)
        events = self._turn()
        text = self._text(events)
        self.assertIn("acme_llm", text, events)
        self.assertIn("refusing", text, events)
        self.assertEqual(events[-1].get("type"), "done")
        self.assertFalse(self.dump.exists(), "the turn must not fall back to the default endpoint")


if __name__ == "__main__":
    unittest.main()
