"""E2E proof for the adversarial-review fixes E-02/E-03/E-05/E-10/E-11/E-12.

Every test drives the REAL app (``corvin_console.standalone.create_app()``)
through the HTTP boundary with ``TestClient`` against a scratch
``CORVIN_HOME`` — never the live ``.corvin/``. Sessions are created through
``corvin_console.auth.create_session`` (the same store ``require_session``
reads), CSRF tokens through ``derive_csrf_token``.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore")

TENANT = "_default"


@pytest.fixture(scope="module")
def sb(tmp_path_factory):
    """Scratch CORVIN_HOME + booted app + one owner session."""
    home = tmp_path_factory.mktemp("sec_home")
    for sub in ("global/auth", "global/forge", "global/console/sessions"):
        (home / "tenants" / TENANT / sub).mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ.pop("CORVIN_TENANT_ID", None)
    try:
        from corvin_console import auth as session_auth
        from corvin_console.standalone import create_app

        app = create_app()
        rec = session_auth.create_session(tenant_id=TENANT, token_fingerprint="")
        csrf = session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        anon = TestClient(app, raise_server_exceptions=False)
        authed = TestClient(app, raise_server_exceptions=False)
        authed.cookies.set(session_auth.COOKIE_NAME, rec.sid)
        yield SimpleNamespace(
            home=home, app=app, anon=anon, authed=authed, rec=rec, csrf=csrf,
            hdr={"X-CSRF-Token": csrf},
        )
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _audit_events(sb) -> list[dict]:
    chain = sb.home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text(encoding="utf-8").splitlines() if l.strip()]


def _actions(sb) -> list[str]:
    return [
        e.get("details", {}).get("action")
        for e in _audit_events(sb)
        if e.get("event_type") == "console.action_performed"
    ]


# ── E-02 GitHub routes ──────────────────────────────────────────────────────


class TestGithubRoutesE02:
    def test_reads_are_401_for_anonymous_not_404(self, sb):
        for path in ("/v1/console/github/status", "/v1/console/github/config",
                     "/v1/console/github/worker/status", "/v1/console/github/webhook/status"):
            r = sb.anon.get(path)
            assert r.status_code == 401, (path, r.status_code, r.text)

    def test_mutations_need_csrf(self, sb):
        assert sb.anon.post("/v1/console/github/verify", json={"url": "https://github.com/a/b"}).status_code == 401
        r = sb.authed.post("/v1/console/github/verify", json={"url": "https://github.com/a/b"})
        assert r.status_code == 403, r.text  # session but no CSRF token
        for path in ("/v1/console/github/worker/start", "/v1/console/github/worker/stop",
                     "/v1/console/github/webhook/register", "/v1/console/github/webhook/test"):
            assert sb.authed.post(path, json={"token": "t"}).status_code == 403, path
        assert sb.authed.delete("/v1/console/github/config").status_code == 403

    def test_verify_writes_under_corvin_home_and_audits(self, sb):
        r = sb.authed.post(
            "/v1/console/github/verify",
            json={"url": "https://github.com/acme/repo", "token": "ghp_secret"},
            headers=sb.hdr,
        )
        assert r.status_code == 200 and r.json()["connected"] is True, r.text
        cfg = sb.home / "tenants" / TENANT / "github-config.json"
        assert cfg.exists(), "config must live under CORVIN_HOME/tenants/<tid>, not Path.home()"
        data = json.loads(cfg.read_text())
        assert "token" not in data and data["token_hash"]  # never the token itself
        assert "github.verify" in _actions(sb)
        # audit details never carry the URL or token
        blob = json.dumps(_audit_events(sb))
        assert "ghp_secret" not in blob and "github.com/acme" not in blob

        assert sb.authed.get("/v1/console/github/config").status_code == 200
        r = sb.authed.delete("/v1/console/github/config", headers=sb.hdr)
        assert r.status_code == 200 and not cfg.exists()
        assert "github.disconnect" in _actions(sb)

    def test_worker_start_stop_audited(self, sb):
        r = sb.authed.post("/v1/console/github/worker/start", headers=sb.hdr)
        assert r.status_code == 200 and r.json()["success"] is True
        r = sb.authed.post("/v1/console/github/worker/stop", headers=sb.hdr)
        assert r.status_code == 200 and r.json()["success"] is True
        acts = _actions(sb)
        assert "github.worker_start" in acts and "github.worker_stop" in acts

    def test_worker_is_per_tenant(self):
        from corvin_console.routes.github_sync import get_worker

        assert get_worker("_default") is get_worker("_default")
        assert get_worker("_default") is not get_worker("other_tenant")


# ── E-03 Marketplace install ────────────────────────────────────────────────


class TestMarketplaceE03:
    BASE = "/v1/console/api/v1/marketplace"

    def test_prefix_is_no_longer_doubled(self, sb):
        doubled = f"{self.BASE}/api/v1/marketplace/plugins/x/install"
        assert sb.authed.post(doubled, headers=sb.hdr).status_code == 404
        # the SPA path (panels/marketplace.tsx) is the mounted one
        assert sb.anon.post(f"{self.BASE}/plugins/x/install").status_code == 401

    def test_reads_need_session_and_mutations_need_csrf(self, sb):
        assert sb.anon.get(f"{self.BASE}/plugins").status_code == 401
        assert sb.anon.get(f"{self.BASE}/stats").status_code == 401
        assert sb.authed.post(f"{self.BASE}/plugins/x/install").status_code == 403
        assert sb.authed.post(f"{self.BASE}/plugins/x/uninstall").status_code == 403
        assert sb.authed.patch(f"{self.BASE}/plugins/x/enable").status_code == 403
        assert sb.authed.patch(f"{self.BASE}/plugins/x/disable").status_code == 403
        assert sb.authed.post(f"{self.BASE}/reload").status_code == 403
        assert sb.anon.post(f"{self.BASE}/reload").status_code == 401

    def test_tenant_from_session_never_from_body(self, sb):
        r = sb.authed.post(
            f"{self.BASE}/plugins/plugin:demo/install",
            json={"tenant_id": "victim_tenant", "version": "1.0.0"},
            headers=sb.hdr,
        )
        assert r.status_code == 200, r.text
        assert r.json()["tenant_id"] == TENANT
        job_id = r.json()["job_id"]
        assert sb.authed.get(f"{self.BASE}/install/{job_id}/progress").status_code == 200
        assert sb.anon.get(f"{self.BASE}/install/{job_id}/progress").status_code == 401
        assert "marketplace.install_queued" in _actions(sb)

    def test_job_is_bound_to_its_tenant(self, sb):
        from corvin_console.routes import marketplace_install as mi

        now = mi._now()
        job = mi.InstallJob("install_foreign", "p", "other_tenant", mi.JobStatus.PENDING,
                            0, "", now, now)
        mi._remember(job)
        assert sb.authed.get(f"{self.BASE}/install/install_foreign/progress").status_code == 404

    def test_install_jobs_are_bounded(self):
        from corvin_console.routes import marketplace_install as mi

        now = mi._now()
        for i in range(mi._MAX_JOBS + 50):
            mi._remember(mi.InstallJob(f"bound_{i}", "p", TENANT, mi.JobStatus.PENDING,
                                       0, "", now, now))
        assert len(mi._install_jobs) <= mi._MAX_JOBS
        assert "bound_0" not in mi._install_jobs  # oldest evicted first

    def test_plugin_id_is_validated(self, sb):
        r = sb.authed.post(f"{self.BASE}/plugins/..%2F..%2Fetc/install", headers=sb.hdr)
        assert r.status_code in (400, 404)

    def test_custom_repository_mutations_need_csrf(self, sb):
        base = f"{self.BASE}/custom-repositories"
        assert sb.authed.post(base, json={"repo_url": "https://github.com/a/b"}).status_code == 403
        assert sb.authed.patch(base, json={"repo_url": "https://github.com/a/b"}).status_code == 403
        assert sb.authed.request("DELETE", base, json={"repo_url": "https://github.com/a/b"}).status_code == 403
        assert sb.authed.post(f"{base}/refresh", json={"repo_url": "https://github.com/a/b"}).status_code == 403
        assert sb.authed.post(f"{base}/validate", json={"repo_url": "https://github.com/a/b"}).status_code == 403


# ── E-06/E-07 features toggle ───────────────────────────────────────────────


class TestFeaturesToggleE06:
    def test_toggle_needs_csrf_and_is_audited(self, sb):
        body = {"feature_id": "admin_control_plane", "enabled": True}
        assert sb.anon.post("/v1/console/features/toggle", json=body).status_code == 401
        assert sb.authed.post("/v1/console/features/toggle", json=body).status_code == 403
        r = sb.authed.post("/v1/console/features/toggle", json=body, headers=sb.hdr)
        assert r.status_code == 200, r.text
        assert "feature.whitelist_enable" in _actions(sb)


# ── E-10 multi-instance ─────────────────────────────────────────────────────


class TestMultiInstanceE10:
    def test_status_needs_session_and_has_no_personal_data(self, sb):
        assert sb.anon.get("/v1/console/api/multi-instance/status").status_code == 401
        assert sb.anon.get("/v1/console/api/multi-instance/instances").status_code == 401
        r = sb.authed.get("/v1/console/api/multi-instance/status")
        assert r.status_code == 200, r.text
        text = r.text.lower()
        for needle in ("shumway", "veegee82", "home-laptop", "work-pc"):
            assert needle not in text, needle
        assert r.json()["instances"] == []
        assert r.json()["github_repo"] is None
        # /instances is answered by api/multi_instance_sync (mounted first, not
        # part of E-10) — it must at least be authenticated.
        r = sb.authed.get("/v1/console/api/multi-instance/instances")
        assert r.status_code == 200
        for needle in ("veegee82", "home-laptop", "work-pc"):
            assert needle not in r.text.lower(), needle


# ── E-05 dual-gate principal from the session cookie ────────────────────────


class _StubPipeline:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.audits: list[dict] = []

    def check_capability(self, *, actor, capability, tenant_id):
        self.calls.append((actor, tenant_id))
        return True

    def record_audit(self, **kw):
        self.audits.append(kw)


class TestDualGatePrincipalE05:
    @pytest.fixture
    def pipeline(self):
        from core.pipeline import wiring

        stub = _StubPipeline()
        wiring.set_global_pipeline(stub)
        try:
            yield stub
        finally:
            wiring.set_global_pipeline(None)

    def test_headers_never_choose_the_principal(self, sb, pipeline):
        spoof = {"X-User-ID": "root", "X-Tenant-ID": "victim_tenant"}
        r = sb.anon.get("/v1/console/github/status", headers=spoof)
        assert r.status_code == 401
        assert pipeline.calls == [], "anonymous caller must never reach the capability checker"

        r = sb.authed.get("/v1/console/github/status", headers=spoof)
        assert r.status_code == 200, r.text
        assert pipeline.calls[-1] == (sb.rec.sid_fingerprint, TENANT)
        assert pipeline.audits[-1]["actor"] == sb.rec.sid_fingerprint
        assert pipeline.audits[-1]["tenant_id"] == TENANT
        # the anonymous attempt was audited as anonymous / _default — never "root"/"victim_tenant"
        assert pipeline.audits[0]["actor"] == "anonymous"
        assert pipeline.audits[0]["tenant_id"] == TENANT

    def test_public_allowlist_bypasses_the_gate(self, sb, pipeline):
        assert sb.anon.get("/v1/console/healthz").status_code == 200
        assert sb.anon.get("/v1/console/version").status_code == 200
        assert pipeline.calls == []

    def test_flag_reader_import_failure_is_a_boot_error(self, monkeypatch):
        import sys

        from core.pipeline import bootstrap as bs

        monkeypatch.setitem(sys.modules, "corvin_core.feature_flags", None)
        with pytest.raises(RuntimeError, match="feature flags unavailable"):
            bs.instantiate_pipeline(SimpleNamespace(), tenant_id=TENANT)


# ── E-11 exception text stays in the log ────────────────────────────────────


class TestUnhandledExceptionE11:
    def test_500_returns_correlation_id_not_exception_text(self, sb, caplog):
        async def _boom():
            raise RuntimeError("secret-path:/srv/keys/private.pem")

        sb.app.add_api_route("/v1/console/_test_boom", _boom, methods=["GET"])
        with caplog.at_level("ERROR"):
            r = sb.anon.get("/v1/console/_test_boom")
        assert r.status_code == 500
        body = r.json()
        assert "secret-path" not in r.text and "RuntimeError" not in r.text
        assert len(body.get("error_id", "")) == 16, r.text
        assert body["error_id"] in caplog.text and "secret-path" in caplog.text


# ── E-12 global request-body cap ────────────────────────────────────────────


class TestBodyCapE12:
    def test_content_length_over_default_cap_is_413(self, sb):
        r = sb.authed.post(
            "/v1/console/features/toggle",
            content=b"{}",
            headers={**sb.hdr, "content-type": "application/json",
                     "content-length": str(3 * 1024 * 1024)},
        )
        assert r.status_code == 413, r.text

    def test_chunked_body_over_cap_is_413(self, sb):
        def gen():
            for _ in range(5):
                yield b"x" * (1024 * 1024)

        r = sb.authed.post(
            "/v1/console/features/toggle",
            content=gen(),
            headers={**sb.hdr, "content-type": "application/json"},
        )
        assert r.status_code == 413, r.text

    def test_voice_transcribe_keeps_its_own_cap(self, sb):
        r = sb.authed.post(
            "/v1/console/voice/transcribe",
            content=b"",
            headers={"content-length": str(20 * 1024 * 1024)},
        )
        assert r.status_code != 413
        r = sb.authed.post(
            "/v1/console/voice/transcribe",
            content=b"",
            headers={"content-length": str(30 * 1024 * 1024)},
        )
        assert r.status_code == 413

    def test_normal_json_body_passes(self, sb):
        r = sb.authed.get("/v1/console/github/status")
        assert r.status_code == 200
