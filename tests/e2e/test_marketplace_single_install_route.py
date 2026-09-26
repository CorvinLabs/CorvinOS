"""ONE plugin install route on the real console — ADR-0892 regression fence.

ADR-0892 allows exactly one plugin install path:
``POST /v1/console/api/v1/marketplace/plugins/{id}/install`` (marketplace_install.py).
On 2026-09-26 (Wave 1c T16) the console still carried parallel ones, all of them
installing nothing real, and they were deleted:

* ``PUT  /v1/console/v1/console/control-plane/plugins/install`` (control_plane_plugins.py)
  — recorded an operator-typed id/name/version in ``~/.corvin/plugins.json``;
* ``POST /v1/console/v1/marketplace/install`` (features_phase2.py) — a 501 stand-in;
* ``routes/marketplace_skills_routes.py`` / ``routes/skill_marketplace_routes.py`` —
  unmounted modules whose install was simulated / a stub;
* a second ``GET /api/v1/marketplace/plugins/{id}`` in marketplace_discovery_routes.py,
  shadowed by marketplace.py's identical route and never dispatched.

Everything goes through the REAL console router, mounted exactly as the gateway
mounts it (``include_router(corvin_console.app.router, prefix="/v1/console")``),
over HTTP with TestClient, against a temp ``CORVIN_HOME``. Route introspection
walks FastAPI 0.141's lazy ``_IncludedRouter`` placeholders recursively, because
``app.routes`` no longer lists included routes and ``app.openapi()`` collapses a
duplicate (method, path) into one entry — which is precisely how the shadowed
duplicate stayed invisible.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# Mirrors Environment=PYTHONPATH in core/gateway/systemd/corvin-webui.service.
for _p in reversed([
    "core/console", "core/gateway", "core/license", "core/compliance",
    "corvin_operator/forge", "corvin_operator/skill-forge",
    "corvin_operator/bridges/shared", "core/plugins",
]):
    if str(REPO / _p) not in sys.path:
        sys.path.insert(0, str(REPO / _p))

_INDEX = REPO.parent / "Corvin-Marketplace" / "index" / "plugins.json"

CANONICAL_INSTALL = "/v1/console/api/v1/marketplace/plugins/{plugin_id}/install"


def _fake_session_record(auth_mod, tenant_id: str):
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(auth_mod.SessionRecord):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(auth_mod, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return auth_mod.SessionRecord(**values)


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    home = tmp_path_factory.mktemp("corvin-home")
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    mp = pytest.MonkeyPatch()
    mp.setenv("CORVIN_HOME", str(home))
    mp.setenv("VOICE_AUDIT_PATH", str(home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"))
    if _INDEX.is_file():
        mp.setenv("CORVIN_MARKETPLACE_INDEX", str(_INDEX))

    from fastapi import FastAPI
    import corvin_console.app as capp
    from corvin_console import auth as auth_mod, deps
    from corvin_console.routes import marketplace

    if _INDEX.is_file():
        marketplace.set_marketplace_index_path(_INDEX)
    app = FastAPI()
    app.include_router(capp.router, prefix="/v1/console")  # as corvin_gateway.app does
    rec = _fake_session_record(auth_mod, "_default")
    app.dependency_overrides[deps.require_session] = lambda: rec
    app.dependency_overrides[deps.require_csrf] = lambda: rec
    yield app
    app.dependency_overrides.clear()
    mp.undo()


@pytest.fixture(scope="module")
def client(gateway):
    from fastapi.testclient import TestClient

    with TestClient(gateway) as c:
        yield c


def _walk(routes):
    """(path, method, endpoint module) for every route, through lazy includes."""
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            for c in list(r.effective_candidates()) + list(r.effective_low_priority_routes()):
                if type(c).__name__ == "_IncludedRouter":
                    yield from _walk([c])
                else:
                    for m in getattr(c.original_route, "methods", None) or ():
                        yield c.path, m, getattr(c.endpoint, "__module__", "")
        else:
            for m in getattr(r, "methods", None) or ():
                yield getattr(r, "path", ""), m, getattr(getattr(r, "endpoint", None), "__module__", "")


@pytest.fixture(scope="module")
def all_routes(gateway):
    routes = list(_walk(gateway.router.routes))
    # Positive control: the walk reached the console's route modules. Without
    # this, "exactly one" and "none left" below would pass on an empty walk.
    assert len(routes) > 500, f"walk found only {len(routes)} routes — introspection broke"
    assert (CANONICAL_INSTALL, "POST", "corvin_console.routes.marketplace_install") in routes
    return routes


# ── (c) exactly one plugin install route ─────────────────────────────


def test_exactly_one_plugin_install_route_is_registered(all_routes):
    installs = sorted({
        (p, m) for p, m, _ in all_routes
        if p.endswith("/install") and m in ("POST", "PUT")
        and ("marketplace" in p or "/plugins" in p) and "mcp-plugins" not in p
    })
    assert installs == [(CANONICAL_INSTALL, "POST")], installs


def test_no_marketplace_method_and_path_is_registered_twice(all_routes):
    seen: dict[tuple[str, str], list[str]] = {}
    for p, m, mod in all_routes:
        if "marketplace" in p:
            seen.setdefault((p, m), []).append(mod)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dups, dups
    # The plugin detail route is marketplace.py's, and only marketplace.py's.
    assert seen[("/v1/console/api/v1/marketplace/plugins/{plugin_id}", "GET")] == [
        "corvin_console.routes.marketplace"
    ]


# ── (a) the canonical list + install paths answer ─────────────────────


@pytest.mark.skipif(not _INDEX.is_file(), reason="Corvin-Marketplace checkout not beside the repo")
def test_canonical_list_answers_with_the_index(client):
    r = client.get("/v1/console/api/v1/marketplace/plugins?limit=1000")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == len(body["plugins"]) > 0


@pytest.mark.skipif(not _INDEX.is_file(), reason="Corvin-Marketplace checkout not beside the repo")
def test_canonical_install_answers_with_a_named_result(client):
    # An id not in the index: the route is reached and refuses by name — it is
    # not a 404/405 route miss. (The full install→uninstall round trip on disk
    # is tests/e2e/test_marketplace_console_e2e.py.)
    r = client.post(
        "/v1/console/api/v1/marketplace/plugins/plugin:buildin-memory-does_not_exist/install", json={}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert "not in the marketplace index" in r.json()["error"]


# ── (b) the removed duplicates are no longer routed ───────────────────


@pytest.mark.parametrize("method,path", [
    ("PUT", "/v1/console/v1/console/control-plane/plugins/install"),
    ("POST", "/v1/console/v1/marketplace/install"),
    ("GET", "/v1/console/v1/marketplace/skills"),
    ("POST", "/v1/console/marketplace/skills/os.delegation_router/install"),
    ("POST", "/v1/console/v1/console/marketplace/skills/os.delegation_router/install"),
    ("POST", "/v1/console/v1/skills/marketplace/os.delegation_router/install"),
])
def test_removed_duplicate_install_paths_are_not_routed(client, method, path):
    r = client.request(method, path, json={"plugin_id": "x", "name": "x", "version": "1.0.0",
                                           "boot_layer": "installed"})
    assert r.status_code in (404, 405), (method, path, r.status_code, r.text[:200])
