"""Data chapter gaps (release readiness 2026-10-24): Databases credentials, Knowledge
(RAG), Knowledge Hub and the removed DataHub-creator routes - driven through the REAL
console router with ``TestClient`` and a real session, under a temp ``CORVIN_HOME``
and a temp secret vault.

The retrieval provider is a REAL HTTP server (uvicorn on a free loopback port serving the
BM25 sidecar over a few documents), so the provider is reached over the network exactly
as the Query Tester reaches it; nothing in the orchestrator or the query engine is
mocked.

Each test is RED on origin/main:

* credentials - the form's ``PGPASSWORD`` was refused (HTTP 400) by ``byok/secrets``
  and a value stored there never reached the vault the compute runner reads;
* RAG - a provider created through Add Provider was written to ``rag/<id>.yaml`` while the
  orchestrator reads ``registry.json`` + ``manifests/`` (never listed), the health word
  was the circuit state, the badge and the licence gate counted different things;
* Hub - ``hub/analytics/*`` answered "Hub not initialized", ``GET hub/trending`` appended
  to the jsonl on every call, there was no way to import a catalogue entry;
* ``/datahub/projects`` answered (a router nothing in the UI called).
"""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CONSOLE = _HERE.parent
_REPO = _CONSOLE.parents[1]
for _p in [str(_CONSOLE), str(_REPO), str(_REPO / "corvin_operator"), str(_REPO / "corvin_operator" / "forge")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.filterwarnings("ignore")

SIDECAR_DIR = _REPO / "tests" / "e2e" / "release_demo" / "sidecars"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge", "shared", "bridges")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, tenant_id: str = "_default"):
    """Real console router + real session under a temp CORVIN_HOME and a temp vault."""
    home = tmp_path / "corvin_home"
    tenant_home = home / "tenants" / tenant_id
    for sub in ("auth", "forge", "console/sessions"):
        (tenant_home / "global" / sub).mkdir(parents=True, exist_ok=True)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "XDG_CONFIG_HOME",
            "CORVIN_SECRET_VAULT", "CORVIN_AUDIT_ANCHOR_KEY")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    os.environ["CORVIN_SECRET_VAULT"] = str(tmp_path / "xdg" / "corvin-voice" / "secrets.json")
    anchor = tmp_path / "anchor.key"
    anchor.write_bytes(os.urandom(32))
    anchor.chmod(0o600)
    os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(anchor)
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home, tenant_id
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def bm25_server(tmp_path):
    """A real BM25 retrieval server on a loopback port (the provider the wizard registers)."""
    import csv

    import uvicorn

    data = tmp_path / "data"
    (data / "tables").mkdir(parents=True)
    (data / "tables" / "songs.csv").write_text(
        "track_uri,track_name,artist_names,label,release_date\n"
        "syn:track:1,Glasswind Cobbleway,Moravine Zigurd,Parallaxo Records,2016-01-17\n"
        "syn:track:2,Hollowed Flintway,Fendris Bodkin,Malachyte Records,2021-11-01\n"
        "syn:track:3,Ivorywing Harborline,Kestrin Drydock,Fennimorrow Records,2019-05-05\n",
        encoding="utf-8")
    (data / "tables" / "artists.csv").write_text(
        "artist_uri,artist_name\nsyn:artist:1,Moravine Zigurd\n", encoding="utf-8")
    (data / "tables" / "albums.csv").write_text(
        "album_uri,album_name,artist_names,label,release_date\n"
        "syn:album:1,Waystation Ledger,Moravine Zigurd,Parallaxo Records,2016-01-17\n", encoding="utf-8")
    sys.path.insert(0, str(SIDECAR_DIR))
    try:
        import bm25_sidecar
    finally:
        sys.path.remove(str(SIDECAR_DIR))
    app = bm25_sidecar.make_app(bm25_sidecar.Index(bm25_sidecar.load_documents(data)))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "sidecar did not start"
    yield f"http://127.0.0.1:{port}/search"
    server.should_exit = True
    thread.join(timeout=5)


def _create_body(endpoint: str, provider_id: str = "bm25-demo", classification: str = "INTERNAL") -> dict:
    return {
        "provider_id": provider_id, "name": "BM25 demo", "description": "Local BM25 sidecar",
        "author": "tests", "version": "1.0", "endpoint": endpoint, "method": "POST",
        "timeout_ms": 5000, "auth_type": "none", "auth_token_env_var": "",
        "query_format_sample": '{"query": "{query}", "limit": {limit}}',
        "content_path": "results[].content", "score_path": "results[].score",
        "metadata_path": "results[]", "capabilities": ["keyword-search"],
        "data_classification": classification, "compliance_zone": "EU",
    }


def _unlimited_providers(monkeypatch) -> None:
    """Member tier: no provider cap (the free tier allows exactly one)."""
    import corvin_console.routes._rag_license_gate as gate

    monkeypatch.setattr(gate, "_lic_get_limit", lambda key: None)


def _chain_events(home: Path, tenant_id: str = "_default") -> list[dict]:
    path = home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------------------
# Databases: ONE credential path from the Connect form to the compute runner
# --------------------------------------------------------------------------------------

def _encrypt_for_instance(client, plaintext: str) -> str:
    """What the Connect form does in the browser: RSA-OAEP-SHA256 with the instance key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = client.get("/v1/console/byok/pubkey").json()["pubkey_pem"]
    key = serialization.load_pem_public_key(pem.encode())
    ct = key.encrypt(
        plaintext.encode(),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode()


class TestDatabaseCredentials:
    def test_form_credentials_reach_the_runner_env(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, tid):
            # the form's first step: store the encrypted credential
            for name, value in (("PGUSER", "corvin_ro"), ("PGPASSWORD", "s3cret-Value-42")):
                r = client.post(f"/v1/console/data-sources/_secrets/{name}",
                                json={"ciphertext": _encrypt_for_instance(client, value)})
                assert r.status_code == 200, (name, r.status_code, r.text)
                assert r.json() == {"name": name, "stored": True}
            # the form's second step: register the manifest (names only)
            manifest = {
                "dsi_version": "1", "name": "charts-pg", "adapter": "postgresql",
                "config": {"host": "127.0.0.1", "port": 5433, "database": "synthetic_charts"},
                "data_classification": "INTERNAL", "data_residency": "any",
                "secrets": ["PGUSER", "PGPASSWORD"], "read_only": True,
            }
            import corvin_console.routes.data_sources as ds

            ds._lic_get_limit = lambda key: None  # Member tier
            r = client.post("/v1/console/data-sources", json={"manifest": manifest})
            assert r.status_code == 201, r.text
            # the manifest on disk carries NAMES only
            stored = json.loads((home / "tenants" / tid / "datasource_connections" / "charts-pg.json").read_text())
            assert "s3cret-Value-42" not in json.dumps(stored)
            # the compute runner's resolver sees the values the form stored
            from corvin_compute.cli import _resolve_datasource_env

            env = _resolve_datasource_env(home, tid, ["charts-pg"])
            assert env["PGUSER"] == "corvin_ro" and env["PGPASSWORD"] == "s3cret-Value-42"
            assert env["PGHOST"] == "127.0.0.1" and env["PGPORT"] == "5433"
            assert env["PGDATABASE"] == "synthetic_charts"
            # the vault file is mode 0600 and the value is not in the audit chain
            vault = Path(os.environ["CORVIN_SECRET_VAULT"])
            assert oct(vault.stat().st_mode & 0o777) == "0o600"
            chain = json.dumps(_chain_events(home))
            assert "s3cret-Value-42" not in chain
            assert any(e.get("details", {}).get("action") == "datasource.secret_store"
                       for e in _chain_events(home)), "the credential write must be audited"

    @pytest.mark.parametrize("bad", ["ANTHROPIC_API_KEY", "CORVIN_HOME", "PATH", "lowercase", "A"])
    def test_provider_and_system_names_are_refused(self, tmp_path, bad):
        with _sandbox(tmp_path) as (client, home, tid):
            r = client.post(f"/v1/console/data-sources/_secrets/{bad}",
                            json={"ciphertext": _encrypt_for_instance(client, "x")})
            assert r.status_code == 400, (bad, r.text)
            assert not Path(os.environ["CORVIN_SECRET_VAULT"]).exists()

    def test_names_endpoint_lists_names_never_values(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, tid):
            client.post("/v1/console/data-sources/_secrets/MYSQL_PASSWORD",
                        json={"ciphertext": _encrypt_for_instance(client, "pw-123456789")})
            r = client.get("/v1/console/data-sources/_secrets")
            assert r.status_code == 200
            assert r.json() == {"names": ["MYSQL_PASSWORD"]}
            assert "pw-123456789" not in r.text

    def test_undecryptable_ciphertext_is_a_400_and_stores_nothing(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, tid):
            client.get("/v1/console/byok/pubkey")  # creates the keypair
            r = client.post("/v1/console/data-sources/_secrets/PGPASSWORD",
                            json={"ciphertext": base64.b64encode(b"not a ciphertext" * 20).decode()})
            assert r.status_code == 400
            assert not Path(os.environ["CORVIN_SECRET_VAULT"]).exists()

    def test_mysql_env_sets_both_port_variables(self, tmp_path):
        from corvin_compute.fabric.datasources.connection_env import env_for_connection

        env = env_for_connection({"adapter": "mysql", "config": {"host": "h", "port": 3307, "database": "d"}})
        assert env["MYSQL_PORT"] == env["MYSQL_TCP_PORT"] == "3307"


# --------------------------------------------------------------------------------------
# Knowledge (RAG): wizard -> visible -> healthy -> queryable -> counted
# --------------------------------------------------------------------------------------

class TestKnowledgeEndToEnd:
    def test_wizard_provider_is_listed_healthy_and_returns_hits(self, tmp_path, bm25_server):
        with _sandbox(tmp_path) as (client, home, tid):
            r = client.post("/v1/console/custom-provider/create", json=_create_body(bm25_server))
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "created", r.text

            # canonical layout: manifests/ + registry.json, no flat yaml
            rag = home / "tenants" / tid / "global" / "rag"
            assert (rag / "manifests" / "bm25-demo.yaml").is_file()
            assert [p["id"] for p in json.loads((rag / "registry.json").read_text())["providers"]] == ["bm25-demo"]
            assert not (rag / "bm25-demo.yaml").exists()

            # listed on the very next request, with the badge vocabulary
            listed = client.get("/v1/console/rag/providers").json()
            assert [p["id"] for p in listed["providers"]] == ["bm25-demo"], listed
            provider = listed["providers"][0]
            assert provider["health_status"] == "healthy", provider
            assert provider["circuit_state"] == "closed"
            assert provider["data_classification"] == "INTERNAL"
            assert listed["registered_count"] == 1

            # per-card health: only that provider is probed, same vocabulary
            health = client.get("/v1/console/rag/providers/bm25-demo/health").json()
            assert health["health_status"] == "healthy"
            assert client.get("/v1/console/rag/providers/ghost/health").json()["health_status"] == "unknown"

            # a real query over the network returns the ground-truth hit
            q = client.post("/v1/console/rag/query", json={"query": "Glasswind Cobbleway", "limit": 3}).json()
            assert q["items"], q
            assert "Parallaxo Records" in q["items"][0]["content"]
            assert q["providers_queried"] == 1 and q["providers_answered"] == ["bm25-demo"]
            assert q["classification"] == "INTERNAL"  # server-side, from the provider (UI sent nothing)

            # the same query is a cache hit and is not counted twice
            again = client.post("/v1/console/rag/query", json={"query": "Glasswind Cobbleway", "limit": 3}).json()
            assert again["cache_hit"] is True

            # counters reach the provider card and the Statistics route
            listed = client.get("/v1/console/rag/providers").json()["providers"][0]
            assert listed["query_stats"]["total_queries"] == 1
            assert listed["query_stats"]["queries_today"] == 1
            stats = client.get("/v1/console/rag/statistics").json()["statistics"]
            assert stats["total_queries"] == 1
            assert stats["per_provider_stats"]["bm25-demo"]["total_queries"] == 1

            # the audit record keeps its fields (they were cut to the floor before)
            executed = [e for e in _chain_events(home) if e.get("event_type") == "rag.query_executed"]
            assert executed and executed[0]["details"]["classification"] == "INTERNAL"
            assert executed[0]["details"]["result_count"] >= 1
            created = [e for e in _chain_events(home) if e.get("event_type") == "rag.provider_created"]
            assert created and created[0]["details"]["target_id"] == "bm25-demo"

    def test_badge_counter_and_licence_gate_agree(self, tmp_path, bm25_server):
        with _sandbox(tmp_path) as (client, home, tid):
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(bm25_server)).json()["status"] == "created"
            assert client.get("/v1/console/rag/providers").json()["registered_count"] == 1
            # free tier: exactly one provider; the second create is refused by the SAME count
            r = client.post("/v1/console/custom-provider/create",
                            json=_create_body(bm25_server, provider_id="second"))
            assert r.status_code == 402, r.text
            assert r.json()["detail"]["existing"] == 1

    def test_legacy_flat_manifest_is_migrated_and_visible(self, tmp_path, bm25_server):
        with _sandbox(tmp_path) as (client, home, tid):
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(bm25_server)).json()["status"] == "created"
            rag = home / "tenants" / tid / "global" / "rag"
            # simulate an older build: flat file only, no index, no manifests/
            text = (rag / "manifests" / "bm25-demo.yaml").read_text()
            (rag / "manifests" / "bm25-demo.yaml").unlink()
            (rag / "registry.json").unlink()
            (rag / "legacy-demo.yaml").write_text(text)
            listed = client.get("/v1/console/rag/providers").json()
            assert [p["id"] for p in listed["providers"]] == ["legacy-demo"], listed
            assert listed["registered_count"] == 1

    def test_uppercase_id_and_duplicate_are_rejected_with_a_reason(self, tmp_path, bm25_server, monkeypatch):
        with _sandbox(tmp_path) as (client, home, tid):
            _unlimited_providers(monkeypatch)
            r = client.post("/v1/console/custom-provider/create", json=_create_body(bm25_server, "Upper"))
            assert r.status_code == 400
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(bm25_server)).json()["status"] == "created"
            dup = client.post("/v1/console/custom-provider/create", json=_create_body(bm25_server))
            assert dup.status_code == 409 and dup.json()["detail"]["error"] == "provider_exists"

    def test_confidential_provider_is_blocked_by_the_l34_gate(self, tmp_path, bm25_server):
        with _sandbox(tmp_path) as (client, home, tid):
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(bm25_server, classification="CONFIDENTIAL")).json()["status"] == "created"
            q = client.post("/v1/console/rag/query", json={"query": "Glasswind"}).json()
            assert q["blocked_by"] == "L34_DATA_CLASSIFICATION" and q["items"] == []
            blocked = [e for e in _chain_events(home) if e.get("event_type") == "rag.query_blocked"]
            assert blocked and blocked[0]["details"]["classification"] == "CONFIDENTIAL"

    def test_dead_provider_is_unhealthy_not_blank(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, tid):
            dead = f"http://127.0.0.1:{_free_port()}/search"
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(dead)).json()["status"] == "created"
            provider = client.get("/v1/console/rag/providers").json()["providers"][0]
            assert provider["health_status"] == "unhealthy"
            assert provider["error"]

    def test_provider_token_comes_from_the_vault_not_the_environment(self, tmp_path, monkeypatch):
        with _sandbox(tmp_path) as (client, home, tid):
            from shared.rag_service import VaultTokens

            monkeypatch.setenv("MY_RAG_TOKEN", "from-environment")
            assert VaultTokens().get("MY_RAG_TOKEN") is None
            from forge import secret_vault

            secret_vault.set_secret("MY_RAG_TOKEN", "from-vault")
            assert VaultTokens().get("MY_RAG_TOKEN") == "from-vault"
            assert VaultTokens().get("ANTHROPIC_API_KEY") is None


# --------------------------------------------------------------------------------------
# Knowledge Hub: tenant-local catalogue with publish / review / import / export
# --------------------------------------------------------------------------------------

class TestKnowledgeHub:
    def test_publish_review_import_export_roundtrip(self, tmp_path, bm25_server, monkeypatch):
        with _sandbox(tmp_path) as (client, home, tid):
            _unlimited_providers(monkeypatch)
            assert client.post("/v1/console/custom-provider/create",
                               json=_create_body(bm25_server)).json()["status"] == "created"

            # analytics is initialised (it answered "Hub not initialized" before)
            assert client.get("/v1/console/hub/analytics/summary").json()["total_providers"] == 0

            pub = client.post("/v1/console/hub/providers", json={"provider_id": "bm25-demo"})
            assert pub.status_code == 200 and pub.json()["status"] == "published", pub.text
            card = pub.json()["provider"]
            assert card["data_classification"] == "INTERNAL" and card["capabilities"] == ["keyword-search"]
            assert client.post("/v1/console/hub/providers", json={"provider_id": "bm25-demo"}).status_code == 409
            assert client.post("/v1/console/hub/providers", json={"provider_id": "ghost"}).status_code == 404

            listed = client.get("/v1/console/hub/providers").json()
            assert [p["id"] for p in listed["providers"]] == ["bm25-demo"]

            rev = client.post("/v1/console/hub/reviews",
                              json={"provider_id": "bm25-demo", "rating": 4, "text": "works"}).json()
            assert rev["status"] == "added"
            top = client.get("/v1/console/hub/top-rated").json()["providers"][0]
            assert top["rating"] == 4.0 and top["review_count"] == 1

            # importing under the id that is already registered is refused with a reason
            clash = client.post("/v1/console/hub/import", json={"hub_provider_id": "bm25-demo"})
            assert clash.status_code == 409 and clash.json()["detail"]["error"] == "provider_exists"

            # importing under a new id registers a working provider
            imp = client.post("/v1/console/hub/import",
                              json={"hub_provider_id": "bm25-demo", "provider_id": "bm25-copy"})
            assert imp.status_code == 200 and imp.json() == {"status": "imported", "provider_id": "bm25-copy"}
            ids = [p["id"] for p in client.get("/v1/console/rag/providers").json()["providers"]]
            assert ids == ["bm25-copy", "bm25-demo"]
            assert client.get("/v1/console/hub/providers").json()["providers"][0]["download_count"] == 1

            # export returns the real manifest, no invented share link
            exp = client.post("/v1/console/hub/export", json={"provider_id": "bm25-demo"}).json()
            assert exp["status"] == "exported" and "kind: RAGProvider" in exp["manifest_yaml"]
            assert "share_link" not in exp
            summary = client.get("/v1/console/hub/analytics/summary").json()
            assert summary["total_providers"] == 1 and summary["total_reviews"] == 1

    def test_trending_get_does_not_write(self, tmp_path, bm25_server):
        with _sandbox(tmp_path) as (client, home, tid):
            client.post("/v1/console/custom-provider/create", json=_create_body(bm25_server))
            client.post("/v1/console/hub/providers", json={"provider_id": "bm25-demo"})
            jsonl = home / "tenants" / tid / "global" / "rag_hub" / "hub_providers.jsonl"
            before = jsonl.read_text()
            for _ in range(5):
                assert client.get("/v1/console/hub/trending").status_code == 200
                assert client.get("/v1/console/hub/providers").status_code == 200
            assert jsonl.read_text() == before, "reads must not append to the hub store"

    def test_tampered_catalogue_manifest_is_not_importable(self, tmp_path, bm25_server, monkeypatch):
        with _sandbox(tmp_path) as (client, home, tid):
            _unlimited_providers(monkeypatch)
            client.post("/v1/console/custom-provider/create", json=_create_body(bm25_server))
            client.post("/v1/console/hub/providers", json={"provider_id": "bm25-demo"})
            hub_manifests = home / "tenants" / tid / "global" / "rag_hub" / "manifests"
            (next(hub_manifests.glob("*.yaml"))).write_text("kind: Evil\n")
            r = client.post("/v1/console/hub/import",
                            json={"hub_provider_id": "bm25-demo", "provider_id": "evil"})
            assert r.status_code == 404 and r.json()["detail"]["error"] == "manifest_unavailable"


# --------------------------------------------------------------------------------------
# The DataHub-creator router had no UI: it is gone
# --------------------------------------------------------------------------------------

class TestDeadDatahubCreatorRemoved:
    def test_projects_routes_are_gone(self, tmp_path):
        with _sandbox(tmp_path) as (client, home, tid):
            assert client.get("/v1/console/datahub/projects").status_code == 404
            assert client.get("/v1/console/datahub/list").status_code == 200  # the real DataHub stays

    def test_panel_and_package_are_not_in_the_tree(self):
        assert not (_REPO / "core" / "datahub_creator").exists()
        assert not (_CONSOLE / "corvin_console" / "web-next" / "src" / "panels" / "datahub-creator").exists()
        assert not (_CONSOLE / "corvin_console" / "routes" / "datahub_creator_routes.py").exists()
