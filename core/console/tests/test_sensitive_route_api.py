"""HTTP round trip for the sensitive-route settings (T-0218, ADR-2268 Decision 10).

``GET/PUT /v1/console/settings/engine/sensitive-route`` and
``PUT /v1/console/settings/engine/provider-compliance`` through the real router; only session
auth/CSRF and the provider registry are stubbed. The tenant file is real: the tests read it back
and check its mode (the gateway refuses anything wider than 0o600), that other sections survive,
that an unparseable file is never written, and that writer and reader share one home resolver.
"""
from __future__ import annotations

import dataclasses
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import engine_models as em  # noqa: E402
import spawn_gates  # noqa: E402
from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine as ENG  # noqa: E402

HOST = "llm.acme.example"
BASE = "/settings/engine"
CORRUPT = b"spec:\n  data_classification:\n    matrix: [unclosed\n  : : :\n"


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


def _spec(**kw):
    base = dict(id="acme_llm", label="Acme LLM", base_url=f"https://{HOST}/v1", model_source="static",
                credential_env="ACME_KEY", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


DECL = {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external"}


class SensitiveRouteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.addCleanup(self._restore)
        self.path = ENG._tenant_yaml_path("_default")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.providers = {"acme_llm": _spec(), "anthropic": _spec(id="anthropic", label="Anthropic",
                                                                   base_url="https://api.anthropic.com"),
                          "router": _spec(id="router", model_source="openrouter"),
                          "proxied": _spec(id="proxied", proxy_base_url="https://proxy.example/v1"),
                          "bedrock": _spec(id="bedrock", base_url="", auth_mode="platform", credential_env="",
                                           platform_env={"region_env": ["AWS_REGION"], "default_region": "us-east-1",
                                                         "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com"})}
        orig = em.load_providers
        em.load_providers = lambda force_reload=False: self.providers  # type: ignore[assignment]
        self.addCleanup(setattr, em, "load_providers", orig)

    def _restore(self) -> None:
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev

    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENG.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: _fake_record()
        return TestClient(app)

    def stored(self) -> dict:
        return yaml.safe_load(self.path.read_text(encoding="utf-8"))

    def declare(self, **kw):
        return self.client().put(f"{BASE}/provider-compliance", json={**DECL, **kw})

    # ── GET ───────────────────────────────────────────────────────────────

    def test_get_on_a_fresh_tenant_is_an_honest_empty_state(self) -> None:
        r = self.client().get(f"{BASE}/sensitive-route")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"enabled": False, "provider": "", "model_id": "", "eligible_providers": []})
        self.assertFalse(self.path.exists(), "a read must not create the tenant file")

    # ── provider-compliance ───────────────────────────────────────────────

    def test_declaring_a_provider_stores_it_with_the_server_filled_host_at_mode_0600(self) -> None:
        r = self.declare()
        self.assertEqual(r.status_code, 200, r.text)
        entry = self.stored()["spec"]["data_classification"]["provider_compliance"][0]
        self.assertEqual(entry["provider"], "acme_llm")
        self.assertEqual(entry["endpoint_host"], HOST)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_client_supplied_endpoint_host_is_refused(self) -> None:
        r = self.declare(endpoint_host="evil.example")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertFalse(self.path.exists())

    def test_declaring_twice_replaces_and_keeps_other_sections(self) -> None:
        self.path.write_text(yaml.safe_dump({"spec": {"egress": {"enabled": True},
                                                      "engine_models": {"claude_code": {"os_model": "x"}}}}))
        self.path.chmod(0o600)
        self.assertEqual(self.declare().status_code, 200)
        self.assertEqual(self.declare(locality="local", network_egress="local").status_code, 200)
        spec = self.stored()["spec"]
        entries = spec["data_classification"]["provider_compliance"]
        self.assertEqual([(e["provider"], e["locality"]) for e in entries], [("acme_llm", "local")])
        self.assertEqual(spec["egress"], {"enabled": True})
        self.assertEqual(spec["engine_models"]["claude_code"]["os_model"], "x")

    def test_inadmissible_declarations_are_refused_with_a_code(self) -> None:
        cases = [
            ({"locality": "us_cloud"}, "locality_not_admissible"),
            ({"locality": "unknown"}, "locality_not_admissible"),
            ({"network_egress": "none"}, "network_egress_not_admissible"),
            ({"provider": "ghost"}, "unknown_provider"),
            ({"provider": "anthropic"}, "native_provider"),
            ({"provider": "router"}, "aggregator"),
            ({"provider": "proxied"}, "proxy_provider"),
            ({"provider": "bedrock"}, "platform_incomplete"),
        ]
        for patch, code in cases:
            with self.subTest(patch=patch):
                r = self.declare(**patch)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["detail"]["code"], code)
        self.assertFalse(self.path.exists(), "a refused declaration must write nothing")

    def test_a_platform_provider_is_stored_with_its_region_and_models(self) -> None:
        r = self.declare(provider="bedrock", region="eu-central-1", models=["anthropic.claude-sonnet-5"])
        self.assertEqual(r.status_code, 200, r.text)
        entry = self.stored()["spec"]["data_classification"]["provider_compliance"][0]
        self.assertTrue(entry["platform"])
        self.assertEqual(entry["endpoint_host"], "bedrock-runtime.eu-central-1.amazonaws.com")
        self.assertEqual(entry["models"], ["anthropic.claude-sonnet-5"])

    # ── sensitive-route ───────────────────────────────────────────────────

    def test_enabling_a_route_round_trips_and_lists_the_eligible_provider(self) -> None:
        self.assertEqual(self.declare().status_code, 200)
        r = self.client().put(f"{BASE}/sensitive-route",
                              json={"enabled": True, "provider": "acme_llm", "model_id": "vendor/Model-1:v2"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["enabled"], body["provider"], body["model_id"]),
                         (True, "acme_llm", "vendor/Model-1:v2"))
        self.assertEqual([p["provider"] for p in body["eligible_providers"]], ["acme_llm"])
        self.assertEqual(body["eligible_providers"][0]["admitted_classes"], ["INTERNAL", "CONFIDENTIAL"])
        route = self.stored()["spec"]["data_classification"]["sensitive_route"]
        self.assertEqual(route, {"enabled": True, "provider": "acme_llm", "model_id": "vendor/Model-1:v2"})
        again = self.client().get(f"{BASE}/sensitive-route").json()
        self.assertEqual(again["provider"], "acme_llm")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_target_without_a_declared_locality_is_refused(self) -> None:
        r = self.client().put(f"{BASE}/sensitive-route",
                              json={"enabled": True, "provider": "acme_llm", "model_id": "m"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"]["code"], "target_not_eligible")
        self.assertFalse(self.path.exists())

    def test_a_bad_model_id_is_a_policy_error(self) -> None:
        self.assertEqual(self.declare().status_code, 200)
        before = self.path.read_bytes()
        for bad in ("has space", "", "x" * 201, "semi;colon"):
            with self.subTest(model_id=bad):
                r = self.client().put(f"{BASE}/sensitive-route",
                                      json={"enabled": True, "provider": "acme_llm", "model_id": bad})
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["detail"]["code"], "policy_invalid")
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_platform_model_must_be_in_the_declared_models(self) -> None:
        self.declare(provider="bedrock", region="eu-central-1", models=["declared-model"])
        bad = self.client().put(f"{BASE}/sensitive-route",
                                json={"enabled": True, "provider": "bedrock", "model_id": "other-model"})
        self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "target_not_eligible"))
        ok = self.client().put(f"{BASE}/sensitive-route",
                               json={"enabled": True, "provider": "bedrock", "model_id": "declared-model"})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_the_model_id_is_stored_verbatim_not_normalised(self) -> None:
        self.declare()
        mid = "meta-llama/Llama-3.3-70B-Instruct"
        r = self.client().put(f"{BASE}/sensitive-route", json={"enabled": True, "provider": "acme_llm", "model_id": mid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.stored()["spec"]["data_classification"]["sensitive_route"]["model_id"], mid)

    def test_disabling_always_works_even_when_the_provider_became_ineligible(self) -> None:
        self.declare()
        self.client().put(f"{BASE}/sensitive-route", json={"enabled": True, "provider": "acme_llm", "model_id": "m"})
        self.providers["acme_llm"] = _spec(base_url="https://moved.example/v1")      # endpoint changed
        r = self.client().put(f"{BASE}/sensitive-route", json={"enabled": False, "provider": "acme_llm", "model_id": "m"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])
        self.assertFalse(self.stored()["spec"]["data_classification"]["sensitive_route"]["enabled"])
        self.assertEqual(self.client().get(f"{BASE}/sensitive-route").json()["eligible_providers"], [])

    def test_extra_fields_are_refused(self) -> None:
        r = self.client().put(f"{BASE}/sensitive-route",
                              json={"enabled": False, "provider": "p", "model_id": "m", "min_class": "PUBLIC"})
        self.assertEqual(r.status_code, 422, r.text)

    # ── never write over a file we cannot read ────────────────────────────

    def test_an_unparseable_file_refuses_every_route_and_is_never_written(self) -> None:
        self.path.write_bytes(CORRUPT)
        calls = [
            ("get", f"{BASE}/sensitive-route", None),
            ("put", f"{BASE}/sensitive-route", {"enabled": False, "provider": "", "model_id": ""}),
            ("put", f"{BASE}/provider-compliance", DECL),
        ]
        for method, url, body in calls:
            with self.subTest(method=method, url=url):
                r = getattr(self.client(), method)(url, **({"json": body} if body is not None else {}))
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["detail"]["code"], "policy_invalid")
        self.assertEqual(self.path.read_bytes(), CORRUPT, "the corrupt policy file was rewritten")

    def test_a_policy_section_of_the_wrong_shape_is_refused_not_overwritten(self) -> None:
        self.path.write_text("spec:\n  data_classification: [not, a, mapping]\n")
        before = self.path.read_bytes()
        r = self.declare()
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "policy_invalid"))
        self.assertEqual(self.path.read_bytes(), before)

    # ── writer path == reader path ────────────────────────────────────────

    def test_the_writer_reads_and_writes_the_file_the_gate_reads(self) -> None:
        os.environ.pop("CORVIN_HOME", None)
        self.assertEqual(ENG._corvin_home(), spawn_gates._resolve_corvin_home(None))
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.assertEqual(self.declare().status_code, 200)
        gate_path = spawn_gates._resolve_corvin_home(None) / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        self.assertEqual(self.path, gate_path)
        self.assertTrue(gate_path.is_file())

    # ── audit ─────────────────────────────────────────────────────────────

    def test_success_and_failure_are_audited_under_the_existing_names(self) -> None:
        seen: list[tuple[str, str, str]] = []
        orig_ok, orig_bad = ENG.console_audit.action_performed, ENG.console_audit.action_failed
        ENG.console_audit.action_performed = lambda **kw: seen.append(("ok", kw["action"], kw.get("target_id", "")))
        ENG.console_audit.action_failed = lambda **kw: seen.append(("fail", kw["action"], kw.get("reason", "")))
        self.addCleanup(setattr, ENG.console_audit, "action_performed", orig_ok)
        self.addCleanup(setattr, ENG.console_audit, "action_failed", orig_bad)
        self.assertEqual(self.declare().status_code, 200)
        self.assertEqual(self.declare(locality="us_cloud").status_code, 422)
        self.assertIn(("ok", "engine_setting_updated", "provider_compliance"), seen)
        self.assertIn(("fail", "engine.setting.update", "locality_not_admissible"), seen)


if __name__ == "__main__":
    unittest.main()
