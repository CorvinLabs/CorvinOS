"""Route-table authentication guard (adversarial review E-01/E-02/E-03/E-06).

Walks EVERY route mounted by ``corvin_console.standalone.create_app()`` and
asserts, from the live dependency tree (not from source grep):

1. every route is authenticated — ``require_session`` / ``require_csrf`` (or a
   token-based ``require_*_or_token`` / ``require_*_csrf`` variant, or a direct
   ``corvin_console_sid`` cookie parameter for WebSockets) is somewhere in its
   dependants — unless the exact path is in ``deps.PUBLIC_ROUTES`` (each entry
   carries the reason it is public by design);
2. every POST/PUT/PATCH/DELETE route carries a CSRF dependency;
3. the public allowlist is not stale: every entry still exists in the route
   table and is still unauthenticated;
4. the dual-gate middleware's ``PUBLIC_PATH_PREFIXES`` covers exactly the
   allowlist and never a session-authenticated route.

``KNOWN_OPEN_*`` lists the violations that other workstreams are fixing
concurrently. Each entry is ASSERTED TO STILL BE OPEN — the moment a fix lands,
``test_known_open_entries_are_still_open`` fails and the entry must be deleted,
so the list can only shrink.

This is the guard the E-01 report asked for: it closes the class ("a mounted
router with a stubbed ``get_current_user``"), not one instance.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Route as StarletteRoute
from starlette.routing import WebSocketRoute as StarletteWebSocketRoute

pytestmark = pytest.mark.filterwarnings("ignore")

MUTATING = ("POST", "PUT", "PATCH", "DELETE")
SESSION_COOKIE = "corvin_console_sid"

#: Violations another workstream is fixing. EMPTY as of 2026-09-07 — the four
#: entries that lived here are all closed:
#:   GET  /v1/console/skill-creator/status/{run_id} → Depends(require_session)
#:   POST /v1/console/skill-creator/generate        → Depends(require_csrf)
#:   POST /v1/console/api/learning/{,un}subscribe   → Depends(require_csrf)
#: The list can only ever SHRINK (see the module docstring): a new violation is
#: a failing test, not a new entry — add one only with the owning workstream
#: named, and delete it the moment the fix lands.
KNOWN_OPEN_UNAUTHENTICATED: dict[tuple[str, str], str] = {}

KNOWN_OPEN_NO_CSRF: dict[tuple[str, str], str] = {}


def _is_auth_dep(name: str) -> bool:
    # require_session / require_csrf and their token-based siblings
    # (require_session_or_token, require_csrf_or_token, require_surface_csrf,
    # require_builder_csrf). ``_optional_session`` is NOT auth — it is optional.
    return name.startswith("require_") and ("session" in name or "csrf" in name)


def _is_csrf_dep(name: str) -> bool:
    return name.startswith("require_") and "csrf" in name


def _dep_names(dependant, acc=None) -> set[str]:
    acc = acc if acc is not None else set()
    for d in dependant.dependencies:
        if d.call is not None:
            acc.add(getattr(d.call, "__name__", repr(d.call)))
        _dep_names(d, acc)
    return acc


def _walk(routes, prefix=""):
    for r in routes:
        if isinstance(r, (APIRoute, APIWebSocketRoute)):
            names = _dep_names(r.dependant)
            cookies = {p.name for p in r.dependant.cookie_params}
            authed = any(_is_auth_dep(n) for n in names) or SESSION_COOKIE in cookies
            csrf = any(_is_csrf_dep(n) for n in names)
            methods = ("WS",) if isinstance(r, APIWebSocketRoute) else tuple(sorted(r.methods))
            yield {
                "methods": methods,
                "path": prefix + r.path,
                "authed": authed,
                "csrf": csrf,
                "deps": sorted(names),
                "module": r.endpoint.__module__,
            }
        elif isinstance(r, (StarletteRoute, StarletteWebSocketRoute)):
            # A BARE starlette route — registered with ``add_route`` rather
            # than a FastAPI decorator, so it has no dependant tree and cannot
            # carry a dependency at all. FastAPI's own ``/openapi.json``,
            # ``/docs`` and ``/redoc`` are exactly this shape, and until
            # 2026-09-07 ``/openapi.json`` answered an anonymous GET with all
            # 496 paths and every schema — invisible to this guard, which only
            # descended into APIRoute/APIWebSocketRoute. Such a route is
            # UNAUTHENTICATED by construction: report it as a row so it has to
            # be either removed or justified in ``PUBLIC_ROUTES``.
            yield {
                "methods": (("WS",) if isinstance(r, StarletteWebSocketRoute)
                            else tuple(sorted(r.methods or ()))),
                "path": prefix + r.path,
                "authed": False,
                "csrf": False,
                "deps": [],
                "module": getattr(r.endpoint, "__module__", "?"),
            }
        elif hasattr(r, "original_router"):
            p = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            yield from _walk(r.original_router.routes, prefix + p)
        elif hasattr(r, "routes"):
            yield from _walk(r.routes, prefix + (getattr(r, "prefix", "") or ""))
        elif hasattr(r, "router"):
            yield from _walk(r.router.routes, prefix + (getattr(r, "prefix", "") or ""))


@pytest.fixture(scope="module")
def route_table(tmp_path_factory):
    home = tmp_path_factory.mktemp("guard_home")
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ.pop("CORVIN_TENANT_ID", None)
    try:
        from corvin_console.standalone import create_app

        app = create_app()
        rows = list(_walk(app.routes))
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert len(rows) > 300, "route walk did not descend into the console router"
    return rows


def _fmt(row) -> str:
    return f"{','.join(row['methods'])} {row['path']} deps={row['deps']} ({row['module']})"


def _keys(row):
    return {(m, row["path"]) for m in row["methods"]}


def _unauth_violations(rows):
    from corvin_console.deps import PUBLIC_ROUTES

    return [r for r in rows if not r["authed"] and r["path"] not in PUBLIC_ROUTES]


def _csrf_violations(rows):
    from corvin_console.deps import PUBLIC_ROUTES

    out = []
    for r in rows:
        if not r["authed"] or r["path"] in PUBLIC_ROUTES:
            continue
        if any(m in MUTATING for m in r["methods"]) and not r["csrf"]:
            out.append(r)
    return out


class TestEveryRouteIsAuthenticated:
    def test_no_unauthenticated_route_outside_allowlist(self, route_table):
        bad = [
            r for r in _unauth_violations(route_table)
            if not (_keys(r) & set(KNOWN_OPEN_UNAUTHENTICATED))
        ]
        assert not bad, "UNAUTHENTICATED routes outside deps.PUBLIC_ROUTES:\n" + "\n".join(
            _fmt(r) for r in bad
        )

    def test_every_mutation_requires_csrf(self, route_table):
        bad = [
            r for r in _csrf_violations(route_table)
            if not (_keys(r) & set(KNOWN_OPEN_NO_CSRF))
        ]
        assert not bad, "MUTATING routes without a CSRF dependency:\n" + "\n".join(
            _fmt(r) for r in bad
        )

    def test_known_open_entries_are_still_open(self, route_table):
        """A KNOWN_OPEN entry that got fixed is stale — delete it (the list only shrinks)."""
        open_unauth = set().union(*(_keys(r) for r in _unauth_violations(route_table)))
        open_nocsrf = set().union(*(_keys(r) for r in _csrf_violations(route_table)))
        stale = [k for k in KNOWN_OPEN_UNAUTHENTICATED if k not in open_unauth]
        stale += [k for k in KNOWN_OPEN_NO_CSRF if k not in open_nocsrf]
        assert not stale, (
            "KNOWN_OPEN entries are now FIXED (or unmounted) — remove them from the "
            f"list in {Path(__file__).name}: {stale}"
        )


class TestAllowlistIsNotStale:
    def test_every_allowlisted_path_exists_and_is_public(self, route_table):
        from corvin_console.deps import PUBLIC_ROUTES

        by_path: dict[str, list] = {}
        for r in route_table:
            by_path.setdefault(r["path"], []).append(r)
        missing = [p for p in PUBLIC_ROUTES if p not in by_path]
        assert not missing, f"PUBLIC_ROUTES names paths that are no longer mounted: {missing}"
        now_authed = [
            p for p in PUBLIC_ROUTES if all(r["authed"] for r in by_path[p])
        ]
        assert not now_authed, (
            "PUBLIC_ROUTES entries are now authenticated — drop them so the "
            f"allowlist cannot silently widen again: {now_authed}"
        )

    def test_every_allowlist_entry_has_a_reason(self):
        from corvin_console.deps import PUBLIC_ROUTES

        for path, reason in PUBLIC_ROUTES.items():
            assert isinstance(reason, str) and len(reason) >= 12, f"{path}: reason missing"


class TestDualGateSkipListMatchesAllowlist:
    def test_prefixes_cover_every_public_route(self):
        from corvin_console.deps import PUBLIC_PATH_PREFIXES, PUBLIC_ROUTES

        uncovered = [
            p for p in PUBLIC_ROUTES
            if p != "/"  # exact root is skipped by wiring.should_skip itself
            and not any(p == pre.rstrip("/") or p.startswith(pre) for pre in PUBLIC_PATH_PREFIXES)
        ]
        assert not uncovered, (
            "public routes the dual-gate middleware would still gate (E-05): "
            f"{uncovered}"
        )

    def test_prefixes_never_cover_a_session_authenticated_route(self, route_table):
        from corvin_console.deps import PUBLIC_PATH_PREFIXES

        leaked = [
            _fmt(r) for r in route_table
            if r["authed"] and any(r["path"].startswith(pre) for pre in PUBLIC_PATH_PREFIXES)
        ]
        assert not leaked, (
            "PUBLIC_PATH_PREFIXES lets the dual-gate middleware skip these "
            f"authenticated routes: {leaked}"
        )

    def test_standalone_uses_the_shared_prefix_list(self):
        src = Path(__file__).resolve().parents[1] / "corvin_console" / "standalone.py"
        text = src.read_text(encoding="utf-8")
        assert "PUBLIC_PATH_PREFIXES" in text
        assert "'/v1/console/login'" not in text, "stale inline skip list is back"


class TestSchemaIsNotAnonymousDocumentation:
    """The OpenAPI schema is the whole attack map — it must not be public.

    Asked through the real HTTP boundary, not the route table: FastAPI mounts
    ``/openapi.json`` with ``add_route``, so it is a bare starlette route with
    no dependant tree and no way to carry a dependency. ``docs_url``/``redoc_url``
    were already ``None`` here; ``openapi_url`` was not, so an anonymous
    ``GET /openapi.json`` returned 200 with all 496 paths and every request /
    response model (found 2026-09-07).
    """

    @pytest.fixture(scope="class")
    def client(self, tmp_path_factory):
        from fastapi.testclient import TestClient

        home = tmp_path_factory.mktemp("schema_home")
        (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        try:
            from corvin_console.standalone import create_app

            with TestClient(create_app(), raise_server_exceptions=False) as c:
                yield c
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    @pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
    def test_no_anonymous_api_documentation(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 404, (
            f"{path} answered {resp.status_code} to an anonymous caller — the "
            "API schema/documentation surface must not be public"
        )
