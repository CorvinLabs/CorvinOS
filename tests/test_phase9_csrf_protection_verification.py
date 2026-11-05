"""
Phase 9 P0: CSRF Protection Verification Tests

Every cookie-authenticated POST/PUT/PATCH/DELETE route that the console
actually serves must carry a CSRF dependency.

Until 2026-11-05 this file scanned route SOURCE for a Flask-style
``@require_csrf`` decorator within six lines above ``@router.post(...)``. The
console is FastAPI: CSRF is enforced through ``Depends(...)`` — per route, via
an ``Annotated`` alias in the signature, or once per module through
``APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])``
(``corvin_console/deps.py``, 2026-09-24). The text scan could see none of
these, so it reported 481 "unprotected" console endpoints that were all
guarded, flagged the token-authenticated A2A routes in ``standalone.py``
(public by design, ``deps.PUBLIC_ROUTES``), and flagged
``core/gateway/routes/btw_routes.py``, which no app mounts (ADR-2220).

The check now walks the LIVE route table of ``standalone.create_app()`` and
inspects each route's resolved dependency tree — the same evidence FastAPI
uses at request time — so a guard attached at any of the three levels
counts, and an unmounted module cannot produce a finding.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATEWAY_ROUTES = REPO_ROOT / "core/gateway/routes"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dep_names(dependant, acc=None) -> set[str]:
    acc = acc if acc is not None else set()
    for d in dependant.dependencies:
        if d.call is not None:
            acc.add(getattr(d.call, "__name__", repr(d.call)))
        _dep_names(d, acc)
    return acc


def _walk(routes, prefix=""):
    from fastapi.routing import APIRoute

    for r in routes:
        if isinstance(r, APIRoute):
            names = _dep_names(r.dependant)
            yield {
                "methods": tuple(sorted(r.methods or ())),
                "path": prefix + r.path,
                "deps": names,
                "module": r.endpoint.__module__,
                "func": r.endpoint.__name__,
            }
        elif hasattr(r, "original_router"):
            p = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            yield from _walk(r.original_router.routes, prefix + p)
        elif hasattr(r, "routes"):
            yield from _walk(r.routes, prefix + (getattr(r, "prefix", "") or ""))


def _is_csrf(name: str) -> bool:
    return name.startswith("require_") and "csrf" in name


@pytest.fixture(scope="module")
def route_table(tmp_path_factory):
    home = tmp_path_factory.mktemp("csrf_home")
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ.pop("CORVIN_TENANT_ID", None)
    try:
        from corvin_console.standalone import create_app

        rows = list(_walk(create_app().routes))
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    # Positive control: the walk must have reached the console routers.
    assert len(rows) > 300, f"route walk reached only {len(rows)} routes"
    return rows


def _unprotected(rows):
    from corvin_console.deps import PUBLIC_ROUTES

    return [
        r for r in rows
        if set(r["methods"]) & MUTATING
        and r["path"] not in PUBLIC_ROUTES
        and not any(_is_csrf(n) for n in r["deps"])
    ]


class TestCSRFProtection:
    """Every mutating console route carries a CSRF dependency."""

    def test_console_routes_have_csrf_protection(self, route_table):
        mutating = [r for r in route_table if set(r["methods"]) & MUTATING]
        assert len(mutating) > 100, "Should have mutation endpoints in console routes"
        bad = _unprotected(route_table)
        assert not bad, f"{len(bad)} endpoints lack CSRF protection:\n" + "\n".join(
            f"{','.join(r['methods'])} {r['path']} ({r['module']}.{r['func']})" for r in bad[:20]
        )

    def test_router_level_guard_is_recognised(self, route_table):
        """Positive control for the router-level guard: a module that attaches
        ``require_session_csrf_on_mutation`` once on its ``APIRouter`` (and not
        per route) must still count as protected."""
        guarded = [
            r for r in route_table
            if r["module"].endswith("routes.memory") and set(r["methods"]) & MUTATING
        ]
        assert guarded, "memory routes not mounted — the control cannot run"
        assert all("require_session_csrf_on_mutation" in r["deps"] for r in guarded)

    def test_console_standalone_has_csrf_protection(self, route_table):
        """Routes defined in standalone.py itself: every mutating one is either
        CSRF-guarded or a token-authenticated public route (A2A)."""
        from corvin_console.deps import PUBLIC_ROUTES

        own = [r for r in route_table
               if r["module"].endswith("standalone") and set(r["methods"]) & MUTATING]
        assert own, "standalone.py defines no mutating route — the check has no subject"
        bad = [r for r in own
               if r["path"] not in PUBLIC_ROUTES and not any(_is_csrf(n) for n in r["deps"])]
        assert not bad, f"{len(bad)} standalone endpoints lack CSRF protection: {bad}"

    def test_gateway_routes_have_csrf_protection(self, route_table):
        """A gateway route module served by the console must be CSRF-guarded;
        one that no app mounts (``btw_routes.py``, ADR-2220) serves nothing."""
        served = {r["module"].rsplit(".", 1)[-1]: r for r in route_table}
        checked = 0
        for py_file in GATEWAY_ROUTES.glob("*.py"):
            if py_file.name.startswith("test_") or py_file.stem not in served:
                continue
            for r in route_table:
                if r["module"].rsplit(".", 1)[-1] == py_file.stem and set(r["methods"]) & MUTATING:
                    checked += 1
                    assert any(_is_csrf(n) for n in r["deps"]), (
                        f"gateway route {r['path']} ({py_file.name}) lacks CSRF protection"
                    )
        # btw_routes is the known unmounted module: it must stay unreachable
        # until it gets a real guard (its handler is a TODO stub).
        assert not any(r["path"] == "/v1/console/btw" for r in route_table)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
