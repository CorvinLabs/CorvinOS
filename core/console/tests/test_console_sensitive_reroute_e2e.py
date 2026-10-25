"""E2E (T-0219 / T-0221, ADR-2268): the console OS turn reroutes a sensitive prompt.

``chat_runtime.stream_turn`` runs against a REAL subprocess (``CORVIN_CLAUDE_BIN``) that writes
the environment and arguments it received. A CONFIDENTIAL prompt (an e-mail address) on a tenant
with an enabled, declared ``sensitive_route`` is spawned on the declared provider with the
declared model verbatim; a public prompt on the same tenant spawns unchanged; an undeclared or
egress-denied route refuses without spawning. Only the provider registry is stubbed (the bundled
registry has no ``acme_llm``); the tenant file, the gates, the subprocess and the audit chain
are real.
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

import yaml

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

PROVIDER_HOST = "llm.acme.example"
MAIL = "Please reply to jane.doe@example.com"


def _policy(*, declared: bool = True, allowed: str = PROVIDER_HOST, model: str = "vendor/Model-1",
            allow_default: bool = False) -> str:
    dcfg = {
        "sensitive_route": {"enabled": True, "provider": "acme_llm", "model_id": model},
        "provider_compliance": ([{"provider": "acme_llm", "locality": "eu_cloud",
                                  "network_egress": "external", "endpoint_host": PROVIDER_HOST}]
                                if declared else []),
    }
    return yaml.safe_dump({"spec": {
        "data_classification": dcfg,
        "egress": {"enabled": True, "default_action": "deny",
                   "allowed_hosts": [allowed] + (["api.anthropic.com"] if allow_default else []),
                   "forbidden_hosts": [] if allow_default else ["api.anthropic.com"]},
    }})


def _drain(agen):
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


@unittest.skipIf(sys.platform.startswith("win"), "POSIX launcher in the harness")
class ConsoleSensitiveRerouteE2E(unittest.TestCase):
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
            "ANTHROPIC_BASE_URL": "https://stale.example",
            "ANTHROPIC_API_KEY": "k-anthropic-original",
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

        env = kit.fake_env(home, "plain_no_children", speedup=50.0)
        real = Path(env["CORVIN_CLAUDE_BIN"])
        self.dump = home / "spawn-env.txt"
        self.argdump = home / "spawn-args.txt"
        wrapper = home / "fake-bin" / "claude-dump"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'env > "{self.dump}"\n'
            f'printf "%s\\n" "$@" > "{self.argdump}"\n'
            f'exec "{real}" "$@"\n',
            encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        env["CORVIN_CLAUDE_BIN"] = str(wrapper)
        os.environ.update(env)

        import engine_models as em  # type: ignore
        providers = {"acme_llm": em.ProviderSpec(
            id="acme_llm", label="Acme LLM", base_url=f"https://{PROVIDER_HOST}/v1",
            model_source="static", credential_env="ACME_LLM_KEY", kind="cloud")}
        orig = em.load_providers
        em.load_providers = lambda force_reload=False: providers  # type: ignore[assignment]
        self.addCleanup(setattr, em, "load_providers", orig)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)

    def _write(self, text: str) -> None:
        import spawn_gates  # type: ignore
        spawn_gates._l34_cache.clear()
        if hasattr(spawn_gates, "_l35_cache"):
            spawn_gates._l35_cache.clear()
        self.cfg.write_text(text, encoding="utf-8")
        self.cfg.chmod(0o600)

    def _turn(self, prompt: str) -> list[dict]:
        sess = self.cr.create_session("_default")
        return _drain(self.cr.stream_turn(sess, prompt))

    def _spawn_env(self) -> dict[str, str]:
        self.assertTrue(self.dump.exists(), "the claude subprocess was never started")
        return dict(line.split("=", 1) for line in self.dump.read_text().splitlines() if "=" in line)

    @staticmethod
    def _text(events: list[dict]) -> str:
        return "".join((e.get("text") or "") for e in events if e.get("type") in ("delta", "result"))

    def test_a_confidential_turn_spawns_on_the_declared_provider_with_the_verbatim_model(self) -> None:
        self._write(_policy())
        events = self._turn(MAIL)
        self.assertNotIn("[security]", self._text(events), events)
        spawned = self._spawn_env()
        self.assertEqual(spawned.get("ANTHROPIC_BASE_URL"), f"https://{PROVIDER_HOST}/v1")
        self.assertEqual(spawned.get("ANTHROPIC_API_KEY"), "k-acme-test")
        self.assertNotIn("stale.example", self.dump.read_text())
        self.assertNotIn("k-anthropic-original", self.dump.read_text())
        args = self.argdump.read_text().splitlines()
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "vendor/Model-1")

    def test_a_public_turn_on_the_same_tenant_does_not_reroute(self) -> None:
        self._write(_policy(allow_default=True))
        self._turn("write a haiku about spring")
        spawned = self._spawn_env()
        self.assertNotEqual(spawned.get("ANTHROPIC_BASE_URL"), f"https://{PROVIDER_HOST}/v1")
        self.assertNotIn("vendor/Model-1", self.argdump.read_text().splitlines())

    def test_an_undeclared_target_refuses_and_never_spawns(self) -> None:
        self._write(_policy(declared=False))
        events = self._turn(MAIL)
        self.assertIn("[", self._text(events))
        self.assertFalse(self.dump.exists(), "a refused sensitive turn must not spawn")

    def test_a_provider_host_the_egress_policy_denies_refuses_and_never_spawns(self) -> None:
        self._write(_policy(allowed="other.example"))
        events = self._turn(MAIL)
        self.assertIn("egress", self._text(events).lower(), events)
        self.assertFalse(self.dump.exists())

    def test_the_reroute_is_audited_without_the_prompt(self) -> None:
        self._write(_policy())
        self._turn(MAIL)
        import data_classification as dc  # type: ignore
        chain = Path(dc._guard_audit_chain("_default", self.home / "corvin"))
        text = chain.read_text(encoding="utf-8")
        self.assertIn("data_flow.rerouted", text)
        self.assertNotIn("jane.doe", text)


if __name__ == "__main__":
    unittest.main()
