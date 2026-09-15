"""E2E + wiring proof for worker-engine model routing — ADR-0759.

Everything here guards a defect that was LIVE on a real install on
2026-09-15 and is cheap to reintroduce. Each class names the defect it locks
out; none of them passes vacuously.

What was broken, in the order a request meets it:

1. ``engine_models._REGISTRY_FILE`` pointed at ``<repo>/operator/bundle/…``,
   a directory the ADR-0730 rename emptied. ``load_providers()`` returned
   ``{}``, so the console's model picker was empty and every provider read as
   "unknown provider".
2. ``corvin_gateway.tenant_config.TenantSpec`` was ``extra="forbid"`` over a
   file the CONSOLE co-writes, and required an AWP envelope the console never
   writes. ``load()`` raised and the dispatcher failed 100% of runs before
   spawning an engine.
3. ``routes/engine.py::_save_tenant_yaml`` wrote at umask, so saving anything
   on the Engine Config page left the file 0o664 — which the gateway's
   fail-closed mode check then refuses. Same outcome: every run failed.
4. ``dispatcher._emit_engine_span`` passed no ``model_id`` and no token
   counts, and ``model_usage._collect`` DROPS an observation with no model
   id — so every delegated worker turn was invisible and unpriced.
5. ``forge.security_events`` had no allowlist entry for the four token fields
   ``acs.engine_completed`` emits, so the field floor dropped them.
6. ``engine_detection.probe_claude_code`` checked the OAuth credentials file
   BEFORE the platform flags, reporting "subscription" on a Bedrock host.
7. ``acs_runtime._strip_worker_secrets`` removed the AWS/GCP/Azure credential
   chain by name shape, leaving a Bedrock-mode worker with nothing to sign
   with.

Transport note: the console assertions go through the real FastAPI router via
TestClient (the actual HTTP boundary); only the session dependency is
overridden. The dispatcher assertions drive the real ``RunDispatcher`` with a
stub engine — a real ``claude -p`` spawn is not admissible in a test suite, and
the boundary under test is dispatcher → span writer → chain → console route,
which the stub exercises in full. The real-LLM proof is recorded in ADR-0759.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
    _REPO / "core" / "gateway",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import engine_models as EM  # noqa: E402
import engine_providers as EP  # noqa: E402
import engine_span as ESPAN  # noqa: E402

from core.console.corvin_console import auth as session_auth  # noqa: E402
from core.console.corvin_console import deps as console_deps  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
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


@pytest.fixture
def console_client():
    """The real console router behind a TestClient, auth dependency stubbed."""
    from core.console.corvin_console.routes import engine_api

    app = FastAPI()
    app.include_router(engine_api.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = (
        lambda: _fake_session_record("_default")
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── 1. the registry is reachable at all ──────────────────────────────


class TestRegistryIsReachable:
    """Defect 1. The whole model picker hangs off this one path."""

    def test_registry_file_exists_on_disk(self):
        assert EM._REGISTRY_FILE.is_file(), (
            f"{EM._REGISTRY_FILE} does not exist — every provider lookup "
            f"silently returns empty and the console offers no models"
        )

    def test_providers_and_engines_are_non_empty(self):
        providers = EM.load_providers(force_reload=True)
        engines = EM.load_registry(force_reload=True)
        assert providers, "provider registry empty"
        assert engines, "engine registry empty"
        assert "claude_code" in engines
        # Positive control: the claude_code picker must actually carry models,
        # not merely exist. An empty picker is the failure mode this guards.
        assert engines["claude_code"].worker_models, "no worker models offered"


# ── 2. platform providers (Bedrock / Vertex / Foundry) ───────────────


class TestPlatformProviders:
    """The ADR-0759 auth_mode axis. A platform provider that is handled like an
    api_key provider does not merely misreport — it breaks authentication."""

    @pytest.mark.parametrize("pid", ["bedrock", "vertex", "foundry"])
    def test_declared_as_platform_with_an_enable_flag(self, pid):
        spec = EM.load_providers(force_reload=True)[pid]
        assert spec.is_platform
        assert spec.platform_env.get("enable_var", "").startswith("CLAUDE_CODE_USE_")
        # A credential CHAIN, never a single key the UI would prompt for.
        assert spec.credential_env == ""

    def test_platform_env_never_sets_an_anthropic_redirect(self):
        """The load-bearing half. ANTHROPIC_BASE_URL + a placeholder key make
        Claude Code speak plain Anthropic to a SigV4 endpoint: every turn 403s,
        and the symptom looks like a credential problem, not a routing one."""
        spec = EM.load_providers(force_reload=True)["bedrock"]
        env = EM.platform_provider_env(spec, {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "eu-central-1",
            "AWS_PROFILE": "prod",
        })
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert env["AWS_PROFILE"] == "prod"
        assert env["AWS_REGION"] == "eu-central-1"

    def test_platform_env_invents_no_credential(self):
        """Only variables the parent actually has are forwarded."""
        spec = EM.load_providers(force_reload=True)["bedrock"]
        env = EM.platform_provider_env(spec, {"CLAUDE_CODE_USE_BEDROCK": "1"})
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "AWS_PROFILE" not in env

    def test_egress_host_is_region_derived_not_a_placeholder(self):
        """L35 must validate the host actually contacted. A platform provider
        has no static base_url, and gating on base_url skipped the check."""
        spec = EM.load_providers(force_reload=True)["bedrock"]
        assert spec.base_url == ""
        url = spec.resolved_host_url({"AWS_REGION": "eu-central-1"})
        assert url == "https://bedrock.eu-central-1.amazonaws.com"
        assert spec.egress_url == spec.resolved_host_url()

    def test_active_platform_provider_reads_the_enable_flag(self):
        assert EM.active_platform_provider({}) is None
        active = EM.active_platform_provider({"CLAUDE_CODE_USE_VERTEX": "1"})
        assert active is not None and active.id == "vertex"

    def test_fetch_reports_missing_credentials_without_egressing(self):
        """A platform source with no credentials must explain itself, never
        raise and never present as a broken provider."""
        spec = EM.load_providers(force_reload=True)["bedrock"]
        result = EP.fetch_models(
            "bedrock", base_url=spec.base_url, model_source=spec.model_source,
            credential_env=spec.credential_env, platform_env=spec.platform_env,
        )
        assert result["reachable"] is False
        assert result["models"] == []
        # The flag is the contract the console renders from; the sentence is
        # for humans and will be reworded, so it is only checked for being
        # present and specific — never matched on.
        assert result.get("credential_absent") is True
        assert len(result.get("error") or "") > 20


class TestSigV4:
    """The Bedrock fetch signs its own requests. Checked against AWS's own
    published derivation so a refactor cannot quietly produce a signature that
    is merely well-shaped."""

    def test_signing_key_matches_the_published_derivation(self):
        import hashlib
        import hmac

        # AWS SigV4 documentation example: secret/date/region/service below
        # derive this exact signing key.
        secret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k = _sign(f"AWS4{secret}".encode(), "20150830")
        k = _sign(k, "us-east-1")
        k = _sign(k, "iam")
        k = _sign(k, "aws4_request")
        assert k.hex() == (
            "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
        )

    def test_headers_are_well_formed_and_cover_the_session_token(self):
        import aws_sigv4

        headers = aws_sigv4.signed_get_headers(
            credentials=aws_sigv4.Credentials(
                access_key="AKIDEXAMPLE", secret_key="secret",
                session_token="sess-tok", source="env"),
            host="bedrock.us-east-1.amazonaws.com", region="us-east-1",
            service="bedrock", canonical_uri="/foundation-models",
        )
        auth = headers["Authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
        assert "/us-east-1/bedrock/aws4_request" in auth
        # A session token that is SENT but not SIGNED is rejected by AWS with a
        # signature error that reads like clock skew, not like a token problem.
        assert "x-amz-security-token" in auth
        assert headers["x-amz-security-token"] == "sess-tok"
        assert len(auth.split("Signature=")[1]) == 64

    def test_signature_changes_with_the_request(self):
        """Negative control: a constant-looking signature would pass every
        shape assertion above."""
        import aws_sigv4

        creds = aws_sigv4.Credentials(
            access_key="AKIDEXAMPLE", secret_key="secret", source="env")
        common = dict(credentials=creds, host="bedrock.us-east-1.amazonaws.com",
                      region="us-east-1", service="bedrock")
        a = aws_sigv4.signed_get_headers(canonical_uri="/foundation-models", **common)
        b = aws_sigv4.signed_get_headers(canonical_uri="/inference-profiles", **common)
        assert a["Authorization"] != b["Authorization"]

    def test_the_signer_module_is_present_at_all(self):
        """It was written on 2026-09-15 and then DELETED by the ADR-0730
        rename sweep, together with the registry entry and the fetch branch
        that used it — while the docs describing all three stayed. A missing
        module here is not a missing feature; it is a feature the docs still
        promise."""
        import aws_sigv4

        assert Path(aws_sigv4.__file__).name == "aws_sigv4.py"
        for fn in ("resolve_region", "resolve_credentials",
                   "signed_get_headers", "has_credential_process"):
            assert callable(getattr(aws_sigv4, fn))


# ── 3. engine detection: platform beats a stale OAuth file ───────────


class TestAuthDetectionPrecedence:
    """Defect 6. A leftover ~/.claude/.credentials.json is enough to mask a
    working Bedrock install, and nothing removes that file on a switch."""

    def _probe(self, monkeypatch, env: dict, creds: "Path | None"):
        import engine_detection as ED

        for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY", "AWS_REGION",
                    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        monkeypatch.setattr(ED, "_claude_settings_env", lambda: {})
        monkeypatch.setattr(ED, "_find_binary", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(ED, "_run", lambda *a, **k: (0, "9.9.9 (Claude Code)", ""))
        monkeypatch.setattr(ED, "_find_claude_credentials", lambda: creds)
        return ED.probe_claude_code()

    @pytest.fixture
    def oauth_max_file(self, tmp_path):
        path = tmp_path / ".credentials.json"
        path.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "x" * 40, "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        }}))
        return path

    def test_bedrock_flag_wins_over_a_stale_oauth_file(self, monkeypatch, oauth_max_file):
        probe = self._probe(
            monkeypatch,
            {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "eu-central-1"},
            oauth_max_file,
        )
        assert probe.credential_source == "bedrock"
        assert "eu-central-1" in (probe.detail or "")

    def test_subscription_plan_is_reported_not_just_subscription(
        self, monkeypatch, oauth_max_file,
    ):
        """Defect 6b: Pro, Max, Team and Enterprise are four different sets of
        limits, and all four used to render as the word "subscription"."""
        probe = self._probe(monkeypatch, {}, oauth_max_file)
        assert probe.credential_source == "subscription"
        assert probe.plan == "max"
        assert probe.rate_limit_tier == "default_claude_max_20x"
        assert "Max" in (probe.detail or "")

    def test_no_token_value_ever_leaves_the_probe(self, monkeypatch, oauth_max_file):
        """The probe reads a file full of credentials; it must return labels
        only."""
        probe = self._probe(monkeypatch, {}, oauth_max_file)
        blob = json.dumps(dataclasses.asdict(probe))
        assert "x" * 40 not in blob


# ── 4. worker secret strip must not disarm a platform host ───────────


class TestWorkerPlatformCredentials:
    """Defect 7. The strip is a NAME-shape match; the only credential a
    Bedrock host has is name-shaped exactly like a secret."""

    def test_strip_then_restore_keeps_aws_and_still_drops_foreign_secrets(self):
        import acs_runtime

        parent = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "eu-central-1",
            "AWS_ACCESS_KEY_ID": "AKIA_TEST",
            "AWS_SECRET_ACCESS_KEY": "shhh",
            "AWS_SESSION_TOKEN": "tok",
            "OPENAI_API_KEY": "must-not-survive",
            "GITHUB_TOKEN": "must-not-survive",
            "PGPASSWORD": "must-not-survive",
        }
        env = dict(parent)
        acs_runtime._strip_worker_secrets(env)
        # Pre-condition the whole test rests on: the strip really does remove
        # the AWS credentials. Without this the restore could be a no-op and
        # the test would still pass.
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "AWS_ACCESS_KEY_ID" not in env

        acs_runtime._restore_platform_credentials(env, parent)
        assert env["AWS_SECRET_ACCESS_KEY"] == "shhh"
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEST"
        assert env["AWS_SESSION_TOKEN"] == "tok"
        for leaked in ("OPENAI_API_KEY", "GITHUB_TOKEN", "PGPASSWORD"):
            assert leaked not in env, f"{leaked} must stay stripped"

    def test_restore_is_a_no_op_off_a_platform_host(self):
        import acs_runtime

        parent = {"AWS_SECRET_ACCESS_KEY": "shhh"}  # no enable flag
        env = dict(parent)
        acs_runtime._strip_worker_secrets(env)
        acs_runtime._restore_platform_credentials(env, parent)
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_acs_writes_to_the_canonical_chain(self):
        """Defect: _audit_path composed ``<global>/audit.jsonl`` by hand, one
        directory above the canonical chain, so every ACS record was off the
        trail the tripwire and the console actually read."""
        import acs_runtime
        from core.paths import tenant_audit_chain

        assert acs_runtime._audit_path("_default") == tenant_audit_chain("_default")


# ── 5. the span schema can carry what pricing needs ──────────────────


class TestEngineSpanSchema:
    """Defect 4/5. A single ``tokens_used`` total cannot be priced: the four
    components bill at four different rates."""

    def test_end_details_carries_the_four_way_split(self):
        details = ESPAN.end_details(
            span_id="s1", role="worker", engine_id="claude_code",
            model_id="claude-sonnet-5", status="ok", duration_ms=1000,
            input_tokens=2, output_tokens=13,
            cache_read_tokens=18_531, cache_write_tokens=60_116,
        )
        assert details["model_id"] == "claude-sonnet-5"
        assert details["tokens_used"] == 2 + 13 + 18_531 + 60_116
        for field in ("input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_write_tokens"):
            assert field in ESPAN.END_FIELDS, (
                f"{field} missing from the positive allowlist — the audit "
                f"field floor drops it and the span reaches the chain unpriceable"
            )

    def test_acs_completion_token_split_is_allowlisted(self):
        """The same floor silently dropped the ACS emitter's split for months."""
        from forge import security_events as sec

        allowed = sec._EVENT_ALLOWLIST["acs.engine_completed"]
        for field in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
            assert field in allowed


# ── 6. dispatcher → chain → console, end to end ──────────────────────


class _StubEngine:
    """Minimal engine. Takes ``model`` and reports a DIFFERENT one in its init
    frame, so the test can tell "what we asked for" from "what actually ran" —
    the span must record the latter."""

    name = "claude_code"

    def __init__(self, reported_model: str):
        self._reported = reported_model
        self.received_model: str | None = None

    def spawn(self, prompt, *, env=None, timeout=120.0, model=None):
        self.received_model = model

        class _Ev:
            def __init__(self, **kw):
                self.type = kw.get("type")
                self.text = kw.get("text", "")
                self.usage = kw.get("usage") or {}
                self.error = kw.get("error")
                self.raw = kw.get("raw")

        yield _Ev(type="session_started",
                  raw={"type": "system", "subtype": "init", "model": self._reported})
        yield _Ev(type="text_delta", text="ok")
        yield _Ev(type="turn_completed", usage={
            "input_tokens": 2, "output_tokens": 13,
            "cache_read_input_tokens": 10_010,
            "cache_creation_input_tokens": 57_353,
        })

    def cancel(self):
        pass


class _LegacyEngine:
    """An engine whose spawn predates the ``model`` keyword. Passing it
    unconditionally raised TypeError inside the worker thread and turned a
    working dispatch into status=failed."""

    name = "claude_code"

    def spawn(self, prompt, *, env=None, timeout=120.0):
        class _Ev:
            type = "turn_completed"
            text = "ok"
            usage: dict = {}
            error = None
            raw = None

        yield _Ev()

    def cancel(self):
        pass


class TestDispatcherWorkerSpan:
    def _collect(self, engine, model):
        from corvin_gateway.dispatcher import RunDispatcher

        disp = RunDispatcher(engine_factory=lambda: engine)
        return disp._spawn_collect(engine=engine, prompt="hi", env={}, model=model)

    def test_model_is_passed_to_an_engine_that_accepts_it(self):
        engine = _StubEngine("claude-sonnet-5")
        out = self._collect(engine, "claude-opus-5")
        assert engine.received_model == "claude-opus-5"
        # The span closes on what RAN, not on what was requested.
        assert out["model"] == "claude-sonnet-5"
        assert out["error"] is None

    def test_legacy_engine_without_the_keyword_still_runs(self):
        engine = _LegacyEngine()
        out = self._collect(engine, "claude-opus-5")
        assert out["error"] is None, (
            "an engine that cannot be steered to a model must still dispatch"
        )

    def test_usage_split_maps_onto_the_span_fields(self):
        from corvin_gateway.dispatcher import RunDispatcher

        split = RunDispatcher._usage_split({
            "input_tokens": 2, "output_tokens": 13,
            "cache_read_input_tokens": 10_010,
            "cache_creation_input_tokens": 57_353,
        })
        assert split == {
            "input_tokens": 2, "output_tokens": 13,
            "cache_read_tokens": 10_010, "cache_write_tokens": 57_353,
        }

    def test_absent_usage_is_zero_never_inferred(self):
        from corvin_gateway.dispatcher import RunDispatcher

        assert RunDispatcher._usage_split(None) == {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        }

    def test_worker_model_comes_from_the_tenant_config(self, tmp_path, monkeypatch):
        """What the console writes is what the dispatcher spawns. These used to
        be two unrelated values: the console offered a worker-model choice that
        applied to ACS delegation only."""
        from corvin_gateway.dispatcher import RunDispatcher

        home = tmp_path / ".corvin"
        cfg = home / "tenants" / "_default" / "global"
        cfg.mkdir(parents=True)
        (cfg / "tenant.corvin.yaml").write_text(
            "spec:\n  engine_models:\n    claude_code:\n"
            "      worker_model: claude-opus-5\n"
        )
        monkeypatch.setenv("CORVIN_HOME", str(home))
        EM.load_providers(force_reload=True)  # drop any cached tenant spec
        assert RunDispatcher._resolve_worker_model("_default", "claude_code") == (
            "claude-opus-5"
        )


# ── 7. the console reports the worker turn ───────────────────────────


class TestConsoleSurfacesWorkerUsage:
    """Defect 4's user-visible half: an observation with no model id was
    DROPPED, so 100% of delegated work vanished from the panel."""

    def _chain(self, tmp_path: Path) -> Path:
        chain = tmp_path / "audit.jsonl"
        rows = [
            {"ts": 1_800_000_000.0, "event_type": "engine.span.start",
             "details": ESPAN.start_details(
                 span_id="spn-1", role="worker", engine_id="claude_code",
                 model_id="claude-sonnet-5", run_id="run_1")},
            {"ts": 1_800_000_005.0, "event_type": "engine.span.end",
             "details": ESPAN.end_details(
                 span_id="spn-1", role="worker", engine_id="claude_code",
                 model_id="claude-sonnet-5", run_id="run_1", status="ok",
                 duration_ms=5000, input_tokens=2, output_tokens=13,
                 cache_read_tokens=18_531, cache_write_tokens=60_116)},
            # A worker span from BEFORE the fix: no model id at all.
            {"ts": 1_800_000_010.0, "event_type": "engine.span.start",
             "details": {"span_id": "spn-2", "role": "worker",
                         "engine_id": "claude_code"}},
            {"ts": 1_800_000_011.0, "event_type": "engine.span.end",
             "details": {"span_id": "spn-2", "role": "worker",
                         "engine_id": "claude_code", "status": "ok",
                         "duration_ms": 1000}},
        ]
        chain.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return chain

    def test_worker_role_is_reported_with_real_tokens(self, tmp_path, monkeypatch):
        from core.console.corvin_console import model_usage as mu

        chain = self._chain(tmp_path)
        monkeypatch.setattr(mu, "_chain_path", lambda tid: chain)
        result = mu.model_usage("_default")

        by_role = {r["role"]: r for r in result["roles"]}
        assert "worker" in by_role, "the delegated worker role is missing entirely"
        worker = by_role["worker"]
        assert worker["turns"] == 2
        assert worker["total_tokens"] == 2 + 13 + 18_531 + 60_116
        assert worker["tokens_reported"] is True

        ids = {m["model_id"]: m for m in result["models"]}
        assert "claude-sonnet-5" in ids
        # The model-less span is KEPT and labelled, not silently dropped.
        assert mu.UNREPORTED_MODEL in ids
        assert ids[mu.UNREPORTED_MODEL]["turns"] == 1

    def test_route_returns_the_same_numbers_over_http(self, tmp_path, monkeypatch, console_client):
        """Same assertion through the real transport — the panel reads this
        endpoint, not the function."""
        from core.console.corvin_console import model_usage as mu

        chain = self._chain(tmp_path)
        monkeypatch.setattr(mu, "_chain_path", lambda tid: chain)

        response = console_client.get("/v1/console/v1/engine/model-usage")
        assert response.status_code == 200
        body = response.json()
        worker = next(r for r in body["roles"] if r["role"] == "worker")
        assert worker["total_tokens"] == 2 + 13 + 18_531 + 60_116

    def test_role_totals_reconcile_with_the_grand_total(self, tmp_path, monkeypatch):
        """Two views of one set of observations must not disagree."""
        from core.console.corvin_console import model_usage as mu

        chain = self._chain(tmp_path)
        monkeypatch.setattr(mu, "_chain_path", lambda tid: chain)
        result = mu.model_usage("_default")

        assert sum(r["turns"] for r in result["roles"]) == result["totals"]["turns"]
        assert sum(r["total_tokens"] for r in result["roles"]) == (
            result["totals"]["total_tokens"]
        )
        assert sum(m["turns"] for m in result["models"]) == result["totals"]["turns"]


# ── 8. the shared tenant file both sides must agree on ───────────────


class TestTenantConfigRoundTrip:
    """Defects 2 and 3, together: they produced the same symptom (every run
    ``failed`` before an engine was spawned) from opposite ends of one file."""

    def _write_console_shaped_yaml(self, home: Path) -> Path:
        """Exactly what routes/engine.py::_save_tenant_yaml produces: a bare
        ``spec:`` mapping, no AWP envelope, with keys the gateway does not own."""
        cfg = home / "tenants" / "_default" / "global"
        cfg.mkdir(parents=True, exist_ok=True)
        path = cfg / "tenant.corvin.yaml"
        path.write_text(
            "spec:\n"
            "  default_engine: claude_code\n"
            "  default_worker_engine: claude_code\n"
            "  engine_models:\n"
            "    claude_code:\n"
            "      os_model: claude-haiku-4-5-20251001\n"
            "      worker_model: claude-opus-5\n"
            "  web_chat:\n"
            "    delegation_enabled: true\n"
            "  learning:\n"
            "    operator_feedback: true\n"
        )
        os.chmod(path, 0o600)
        return path

    def test_gateway_loads_the_file_the_console_writes(self, tmp_path, monkeypatch):
        from corvin_gateway import tenant_config as tc

        self._write_console_shaped_yaml(tmp_path)
        monkeypatch.setattr(tc._forge_paths, "tenant_global_dir",
                            lambda tid: tmp_path / "tenants" / tid / "global")
        cfg = tc.load("_default")
        assert cfg.is_engine_allowed("claude_code") is True
        # The foreign keys survive round-tripping rather than being rejected.
        assert (cfg.spec.model_extra or {}).get("default_worker_engine") == "claude_code"

    def test_a_typo_in_a_security_block_is_still_rejected(self):
        """The relaxation is scoped. data_residency gates engine admission; a
        misspelled key there must keep failing loudly, not be accepted as an
        extra and silently disable the restriction."""
        from corvin_gateway.tenant_config import TenantConfig

        with pytest.raises(Exception):
            TenantConfig.model_validate(
                {"spec": {"data_residency": {"forbid_engine": ["claude_code"]}}})
        with pytest.raises(Exception):
            TenantConfig.model_validate({"spec": {"budget": {"max_runs_per_dya": 5}}})

    def test_a_foreign_envelope_is_still_rejected(self):
        from corvin_gateway.tenant_config import TenantConfig

        with pytest.raises(Exception):
            TenantConfig.model_validate({"apiVersion": "other/v2", "kind": "Tenant"})
        with pytest.raises(Exception):
            TenantConfig.model_validate({"kind": "Secret"})

    def test_console_writer_keeps_mode_0600(self, tmp_path, monkeypatch):
        """The gateway refuses anything wider. Writing at umask (0o664) did not
        relax a permission — it disabled every run until someone chmod'ed the
        file back by hand."""
        from core.console.corvin_console.routes import engine as engine_route

        cfg_dir = tmp_path / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True)
        path = cfg_dir / "tenant.corvin.yaml"
        path.write_text("spec: {}\n")
        os.chmod(path, 0o600)

        monkeypatch.setattr(engine_route, "_corvin_home", lambda: tmp_path)
        engine_route._save_tenant_yaml("_default", {"spec": {"default_engine": "claude_code"}})

        assert path.stat().st_mode & 0o777 == 0o600
        assert "claude_code" in path.read_text()

    def test_writer_leaves_no_temp_file_behind(self, tmp_path, monkeypatch):
        from core.console.corvin_console.routes import engine as engine_route

        cfg_dir = tmp_path / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True)
        monkeypatch.setattr(engine_route, "_corvin_home", lambda: tmp_path)
        engine_route._save_tenant_yaml("_default", {"spec": {}})

        leftovers = [p.name for p in cfg_dir.iterdir()
                     if p.name != "tenant.corvin.yaml"]
        assert leftovers == []


# ── 9. the claude-models catalogue, over HTTP ────────────────────────


class TestClaudeModelsRoute:
    def test_every_claude_capable_provider_is_queried_and_reports_itself(
        self, console_client,
    ):
        """The provider list used to be a hardcoded pair, so a provider added
        to the registry was configurable but never asked — its models never
        appeared and its reachability was never reported."""
        response = console_client.get("/v1/console/v1/engine/claude-models")
        assert response.status_code == 200
        body = response.json()

        source_ids = {s["id"] for s in body["sources"]}
        assert {"registry", "anthropic_live", "bedrock_live",
                "vertex_live", "foundry_live"} <= source_ids

        # Positive control: an empty catalogue would satisfy every assertion
        # about sources while telling the operator nothing.
        assert body["count"] > 0, "no Claude model offered at all"
        assert body["default_model_id"]

        for source in body["sources"]:
            if not source["reachable"]:
                assert source["error"], (
                    f"source {source['id']} is unreachable without saying why"
                )
